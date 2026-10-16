import Foundation

enum SonioxProtocolError: Error, LocalizedError, Equatable {
    case invalidEndpoint
    case invalidMessage
    case invalidStartMessage

    var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            return "Failed to build Soniox WebSocket URL"
        case .invalidMessage:
            return "Invalid Soniox streaming message"
        case .invalidStartMessage:
            return "Invalid Soniox start message"
        }
    }
}

struct SonioxTranscriptUpdate: Sendable, Equatable {
    let finalizedText: String
    let partialText: String
}

struct SonioxServerError: Sendable, Equatable {
    let code: Int
    let message: String
}

/// Combined result from a single Soniox server message.
/// A message can carry tokens AND finished simultaneously.
struct SonioxServerMessage: Sendable, Equatable {
    let transcript: SonioxTranscriptUpdate?
    let isFinished: Bool
    let error: SonioxServerError?
}

enum SonioxProtocol {

    private static let endpoint = "wss://stt-rt.soniox.com/transcribe-websocket"
    private static let ignoredMarkerTokens: Set<String> = ["<end>", "<fin>"]

    static func buildWebSocketURL() throws -> URL {
        let urlString = endpoint
        guard let url = URL(string: urlString) else {
            throw SonioxProtocolError.invalidEndpoint
        }
        return url
    }

    static func buildStartMessage(
        config: SonioxASRConfig,
        options: ASRRequestOptions
    ) throws -> String {
        var payload: [String: Any] = [
            "model": config.model,
            "audio_format": "pcm_s16le",
            "sample_rate": 16000,
            "num_channels": 1,
            "enable_endpoint_detection": true,
            "max_endpoint_delay_ms": 3000,
            "language_hints": ["zh", "en"],
            "language_hints_strict": true,
        ]
        payload["api_key"] = config.apiKey

        var context: [String: Any] = [
            "general": [
                ["key": "domain", "value": "voice input"],
                ["key": "topic", "value": "general dictation and tech discussion"],
            ],
        ]

        let terms = sanitizedTerms(from: options.hotwords)
        if !terms.isEmpty {
            context["terms"] = terms
        }
        payload["context"] = context

        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload, options: []),
              let message = String(data: data, encoding: .utf8)
        else {
            throw SonioxProtocolError.invalidStartMessage
        }

        return message
    }

    static func endOfStreamFrame() -> Data {
        Data()
    }

    static func finalizeMessage() -> String {
        #"{"type":"finalize"}"#
    }

    /// Parse a server message, returning all present information at once.
    /// A single message can carry tokens AND `finished: true` simultaneously.
    static func parseServerMessage(from data: Data) throws -> SonioxServerMessage {
        let decoder = JSONDecoder()
        let response = try decoder.decode(Response.self, from: data)

        let serverError: SonioxServerError?
        if let code = response.errorCode {
            serverError = SonioxServerError(
                code: code,
                message: response.errorMessage ?? "Soniox request failed"
            )
        } else {
            serverError = nil
        }

        let finalText = visibleText(from: response.tokens ?? [], isFinal: true)
        let partialText = visibleText(from: response.tokens ?? [], isFinal: false)
        let transcript: SonioxTranscriptUpdate?
        if !finalText.isEmpty || !partialText.isEmpty {
            transcript = SonioxTranscriptUpdate(
                finalizedText: finalText,
                partialText: partialText
            )
        } else {
            transcript = nil
        }

        return SonioxServerMessage(
            transcript: transcript,
            isFinished: response.finished == true,
            error: serverError
        )
    }

    private static func sanitizedTerms(from hotwords: [String]) -> [String] {
        hotwords
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private static func visibleText(from tokens: [Token], isFinal: Bool) -> String {
        tokens
            .filter { ($0.isFinal ?? false) == isFinal }
            .compactMap { token -> String? in
                guard let text = token.text, !ignoredMarkerTokens.contains(text) else {
                    return nil
                }
                return text
            }
            .joined()
    }

    private struct Response: Decodable {
        let tokens: [Token]?
        let finished: Bool?
        let errorCode: Int?
        let errorMessage: String?

        enum CodingKeys: String, CodingKey {
            case tokens
            case finished
            case errorCode = "error_code"
            case errorMessage = "error_message"
        }
    }

    private struct Token: Decodable {
        let text: String?
        let isFinal: Bool?

        enum CodingKeys: String, CodingKey {
            case text
            case isFinal = "is_final"
        }
    }
}
