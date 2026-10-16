import Foundation
import os

/// Local Qwen3-ASR client with periodic live previews and final transcription.
actor SenseVoiceWSClient: SpeechRecognizer {

    private let logger = Logger(subsystem: "com.voxlog.asr", category: "SenseVoiceWS")

    private var eventContinuation: AsyncStream<RecognitionEvent>.Continuation?
    private var _events: AsyncStream<RecognitionEvent>?

    private let http: URLSession

    init(http: URLSession = .shared) {
        self.http = http
    }

    private var confirmedSegments: [String] = []

    // Qwen3 incremental speculative transcription
    private var qwen3DebounceTask: Task<Void, Never>?
    private var allAudioData: Data = Data()
    private var qwen3ConfirmedOffset: Int = 0
    private var qwen3ConfirmedSegments: [String] = []
    private var qwen3LatestText: String?
    private var qwen3HasPendingAudio: Bool = false

    var events: AsyncStream<RecognitionEvent> {
        if let existing = _events { return existing }
        let (stream, continuation) = AsyncStream<RecognitionEvent>.makeStream()
        self.eventContinuation = continuation
        self._events = stream
        return stream
    }

    // MARK: - Connect

    func connect(config: any ASRProviderConfig, options: ASRRequestOptions) async throws {
        // Fresh event stream
        let (stream, continuation) = AsyncStream<RecognitionEvent>.makeStream()
        self.eventContinuation = continuation
        self._events = stream
        confirmedSegments = []
        resetQwen3State()

        if SenseVoiceServerManager.currentQwen3Port != nil {
            // Qwen3 available, wait for health
            let mgr = SenseVoiceServerManager.shared
            var healthy = false
            for _ in 0..<30 {
                if await mgr.isHealthy() { healthy = true; break }
                try await Task.sleep(for: .seconds(1))
            }
            guard healthy, let readyPort = SenseVoiceServerManager.currentQwen3Port else {
                throw SenseVoiceWSError.serverNotHealthy
            }
            eventContinuation?.yield(.ready)
            logger.info("Qwen3 ASR client ready (port \(readyPort))")
        } else {
            // Qwen3 not started, try to start
            let q3Enabled = UserDefaults.standard.object(forKey: "tf_qwen3FinalEnabled") as? Bool ?? true
            if !q3Enabled {
                throw SenseVoiceWSError.allModelsDisabled
            }
            try await SenseVoiceServerManager.shared.start()
            if SenseVoiceServerManager.currentQwen3Port == nil {
                throw SenseVoiceWSError.serverNotRunning
            }
            try await connect(config: config, options: options)
            return
        }
    }

    // MARK: - Send Audio

    func sendAudio(_ data: Data) async throws {
        // Accumulate audio for Qwen3 final
        allAudioData.append(data)
        qwen3HasPendingAudio = true
        scheduleSpeculativeQwen3()
    }

    // MARK: - End Audio

    func endAudio() async throws {
        let pending = qwen3DebounceTask
        pending?.cancel()
        await pending?.value
        qwen3DebounceTask = nil

        let port = SenseVoiceServerManager.currentQwen3Port

        if let port, allAudioData.count > 3200 {
            let newAudioBytes = allAudioData.count - qwen3ConfirmedOffset
            let hasQwen3Result = !qwen3ConfirmedSegments.isEmpty
            let newAudioTrivial = newAudioBytes < 2 * 16000 * 2

            let finalText: String
            if hasQwen3Result && newAudioTrivial {
                // Speculative covered most audio, just handle the tail
                var assembled = qwen3ConfirmedSegments.joined()
                if newAudioBytes > 3200 {
                    if let tailText = await qwen3Transcribe(audio: Data(allAudioData.suffix(from: qwen3ConfirmedOffset)), port: port, timeout: 10) {
                        assembled += tailText
                    }
                }
                finalText = assembled
                DebugFileLogger.log("Qwen3 final: incremental (\(qwen3ConfirmedSegments.count) segments + tail)")
            } else {
                // No speculative, send full audio
                DebugFileLogger.log("Qwen3 full final: sending \(allAudioData.count) bytes")
                finalText = await qwen3Transcribe(audio: Data(allAudioData), port: port, timeout: 30) ?? ""
                DebugFileLogger.log("Qwen3 full final: \(finalText.count) chars")
            }

            if !finalText.isEmpty {
                confirmedSegments = [finalText]
                let transcript = RecognitionTranscript(
                    confirmedSegments: confirmedSegments,
                    partialText: "",
                    authoritativeText: finalText,
                    isFinal: true
                )
                eventContinuation?.yield(.transcript(transcript))
            } else {
                DebugFileLogger.log("Qwen3 final failed, empty result")
            }
            eventContinuation?.yield(.completed)
        } else {
            // No Qwen3 port or audio too short
            DebugFileLogger.log("endAudio: no Qwen3 port or audio too short")
            eventContinuation?.yield(.completed)
        }

        resetQwen3State()
    }

    /// POST audio to Qwen3 /transcribe and return text, or nil on failure.
    private func qwen3Transcribe(audio: Data, port: Int, timeout: TimeInterval) async -> String? {
        let url = URL(string: "http://127.0.0.1:\(port)/transcribe")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.httpBody = audio
        request.timeoutInterval = timeout
        guard let (data, _) = try? await http.data(for: request),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = json["text"] as? String, !text.isEmpty else { return nil }
        return text
    }

    // MARK: - Qwen3 Speculative

    private func scheduleSpeculativeQwen3() {
        // Audio keeps arriving even during silence. Debouncing each packet would
        // postpone recognition forever; allow one scheduled/in-flight request.
        guard qwen3DebounceTask == nil else { return }
        qwen3DebounceTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            guard let self else { return }
            await self.transcribePendingAudio()
        }
    }

    private func transcribePendingAudio() async {
        defer { qwen3DebounceTask = nil }
        guard !Task.isCancelled else { return }
        guard self.qwen3HasPendingAudio else { return }
        guard let port = SenseVoiceServerManager.currentQwen3Port else { return }

        let deltaAudio = self.allAudioData.suffix(from: self.qwen3ConfirmedOffset)
        guard deltaAudio.count > 3200 else { return }  // at least 100ms of audio

        // Snapshot the offset before the HTTP round-trip so audio arriving
        // during the request doesn't get silently marked as processed.
        let offsetSnapshot = self.allAudioData.count

        let url = URL(string: "http://127.0.0.1:\(port)/transcribe")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(deltaAudio)
        request.timeoutInterval = 30

        DebugFileLogger.log("Qwen3 speculative: sending \(deltaAudio.count) bytes (offset \(self.qwen3ConfirmedOffset))")

        do {
            let (data, _) = try await self.http.data(for: request)
            guard !Task.isCancelled else { return }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let text = json["text"] as? String, !text.isEmpty {
                self.confirmQwen3Segment(text, offset: offsetSnapshot)
            }
        } catch {
            DebugFileLogger.log("Qwen3 speculative: failed \(error)")
        }
    }

    private func confirmQwen3Segment(_ text: String, offset: Int) {
        qwen3ConfirmedSegments.append(text)
        qwen3ConfirmedOffset = offset
        qwen3LatestText = nil
        qwen3HasPendingAudio = allAudioData.count > offset

        // Emit live transcript so the UI shows real-time text
        let fullText = qwen3ConfirmedSegments.joined()
        let transcript = RecognitionTranscript(
            confirmedSegments: [fullText],
            partialText: "",
            authoritativeText: fullText,
            isFinal: false
        )
        eventContinuation?.yield(.transcript(transcript))

        DebugFileLogger.log("Qwen3 speculative: confirmed segment \(qwen3ConfirmedSegments.count): \(text.count) chars")
    }

    private func resetQwen3State() {
        allAudioData = Data()
        qwen3ConfirmedOffset = 0
        qwen3ConfirmedSegments = []
        qwen3LatestText = nil
        qwen3HasPendingAudio = false
    }

    // MARK: - Disconnect

    func disconnect() async {
        qwen3DebounceTask?.cancel()
        qwen3DebounceTask = nil
        eventContinuation?.finish()
        eventContinuation = nil
        _events = nil
        logger.info("Qwen3 ASR client disconnected")
    }
}

// MARK: - Errors

enum SenseVoiceWSError: Error, LocalizedError {
    case serverNotRunning
    case serverNotHealthy
    case allModelsDisabled

    var errorDescription: String? {
        switch self {
        case .serverNotRunning:
            return L("识别服务未启动", "ASR server not running")
        case .serverNotHealthy:
            return L("识别服务未就绪", "ASR server not ready")
        case .allModelsDisabled:
            return L("请先在设置中启动识别模型", "Please start an ASR model in Settings")
        }
    }
}
