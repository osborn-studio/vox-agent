import Foundation
import os

/// Qwen3-ASR-Flash streaming: send complete audio, get text back chunk by chunk (SSE).
/// Works with DashScope cloud (US/CN) and local MLX server (same SSE format).
actor QwenFlashStreamASR {

    static let shared = QwenFlashStreamASR()
    private let logger = Logger(subsystem: "com.voxlog.asr", category: "QwenFlash")

    struct Config {
        let apiKey: String
        let model: String
        let baseURL: String

        static var dashscopeCN: (String) -> Config = { apiKey in
            Config(apiKey: apiKey, model: "qwen3-asr-flash",
                   baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1")
        }
        static var dashscopeUS: (String) -> Config = { apiKey in
            Config(apiKey: apiKey, model: "qwen3-asr-flash-us",
                   baseURL: "https://dashscope-us.aliyuncs.com/compatible-mode/v1")
        }
        static func local(port: Int) -> Config {
            Config(apiKey: "local", model: "qwen3-asr-local",
                   baseURL: "http://127.0.0.1:\(port)/v1")
        }
    }

    /// Transcribe with streaming SSE. onChunk receives accumulated text as each chunk arrives.
    func transcribeStreaming(
        audioData: Data,
        config: Config,
        onChunk: @escaping @Sendable (String) -> Void
    ) async throws -> String {
        let audioBase64 = audioData.base64EncodedString()

        let requestBody: [String: Any] = [
            "model": config.model,
            "stream": true,
            "messages": [[
                "role": "user",
                "content": [[
                    "type": "input_audio",
                    "input_audio": [
                        "data": "data:audio/wav;base64,\(audioBase64)",
                        "format": "wav"
                    ]
                ]]
            ]]
        ]

        let url = URL(string: "\(config.baseURL)/chat/completions")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        if config.apiKey != "local" {
            request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: requestBody)
        request.timeoutInterval = 60

        logger.info("Sending \(audioData.count) bytes to \(config.model) at \(config.baseURL)")

        // Use delegate-based URLSession for true streaming
        let result = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
            let handler = SSEStreamHandler(
                onChunk: onChunk,
                onComplete: { fullText in
                    continuation.resume(returning: fullText)
                },
                onError: { error in
                    continuation.resume(throwing: error)
                },
                logger: logger
            )
            let session = URLSession(configuration: .default, delegate: handler, delegateQueue: nil)
            let task = session.dataTask(with: request)
            handler.session = session
            task.resume()
        }

        return result
    }

    enum QwenFlashError: Error, LocalizedError {
        case invalidResponse
        case httpError(Int, String)
        var errorDescription: String? {
            switch self {
            case .invalidResponse: return "Invalid response from Qwen ASR"
            case .httpError(let code, let body): return "Qwen ASR HTTP \(code): \(body.prefix(200))"
            }
        }
    }
}

// MARK: - SSE Stream Handler (URLSessionDataDelegate for true streaming)

private final class SSEStreamHandler: NSObject, URLSessionDataDelegate, @unchecked Sendable {

    let onChunk: @Sendable (String) -> Void
    let onComplete: @Sendable (String) -> Void
    let onError: @Sendable (Error) -> Void
    let logger: Logger
    var session: URLSession?

    private var buffer = ""
    private var fullText = ""
    private var httpStatusCode: Int = 0
    private var errorBody = ""

    init(onChunk: @escaping @Sendable (String) -> Void,
         onComplete: @escaping @Sendable (String) -> Void,
         onError: @escaping @Sendable (Error) -> Void,
         logger: Logger) {
        self.onChunk = onChunk
        self.onComplete = onComplete
        self.onError = onError
        self.logger = logger
    }

    // Called when HTTP response headers arrive
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        httpStatusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        logger.info("SSE response: HTTP \(self.httpStatusCode)")
        completionHandler(.allow)
    }

    // Called for each chunk of data as it arrives (NOT buffered!)
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let text = String(data: data, encoding: .utf8) else { return }

        if httpStatusCode != 200 {
            errorBody += text
            return
        }

        buffer += text

        // Process complete SSE lines
        while let lineEnd = buffer.range(of: "\n") {
            let line = String(buffer[buffer.startIndex..<lineEnd.lowerBound])
                .trimmingCharacters(in: .whitespaces)
            buffer.removeSubrange(buffer.startIndex...lineEnd.lowerBound)

            guard line.hasPrefix("data: ") else { continue }
            let jsonStr = String(line.dropFirst(6))

            if jsonStr == "[DONE]" {
                logger.info("SSE stream done: \(self.fullText.count) chars")
                continue
            }

            guard let jsonData = jsonStr.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                  let choices = json["choices"] as? [[String: Any]],
                  let delta = choices.first?["delta"] as? [String: Any],
                  let content = delta["content"] as? String,
                  !content.isEmpty
            else { continue }

            fullText += content
            onChunk(fullText)
            logger.debug("SSE chunk: +\"\(content)\" → total \(self.fullText.count) chars")
        }
    }

    // Called when the request completes
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.invalidateAndCancel()

        if let error {
            logger.error("SSE error: \(error.localizedDescription)")
            onError(error)
            return
        }

        if httpStatusCode != 200 {
            let err = QwenFlashStreamASR.QwenFlashError.httpError(httpStatusCode, errorBody)
            logger.error("SSE HTTP error: \(self.httpStatusCode)")
            onError(err)
            return
        }

        onComplete(fullText)
    }
}
