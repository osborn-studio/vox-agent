import XCTest
import Foundation
@testable import VoxLog

private final class TranscriptResponse: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"text":"实时识别"}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class SenseVoiceLiveTranscriptTests: XCTestCase {
    func testContinuousAudioProducesTextBeforeRecordingStops() async throws {
        let environment = ProcessInfo.processInfo.environment
        let realPort = environment["VOX_TEST_ASR_PORT"].flatMap(Int.init)
        let config = URLSessionConfiguration.ephemeral
        if realPort == nil { config.protocolClasses = [TranscriptResponse.self] }
        let audio: Data
        if realPort != nil, let path = environment["VOX_TEST_ASR_PCM"] {
            audio = try Data(contentsOf: URL(fileURLWithPath: path))
        } else {
            audio = Data(repeating: 0, count: 30 * 6400)
        }
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let previousPort = SenseVoiceServerManager.currentQwen3Port
        SenseVoiceServerManager.currentQwen3Port = realPort ?? 12345
        defer { SenseVoiceServerManager.currentQwen3Port = previousPort }
        let client = SenseVoiceWSClient(http: session)
        let events = await client.events
        let live = expectation(description: "Live transcript while audio continues")
        live.assertForOverFulfill = false
        live.expectedFulfillmentCount = 2
        let final = expectation(description: "Final transcript after stopping")
        let reader = Task {
            for await event in events {
                if case .transcript(let text) = event, !text.isFinal, !text.displayText.isEmpty {
                    live.fulfill()
                }
                if case .transcript(let text) = event, text.isFinal, !text.displayText.isEmpty {
                    final.fulfill()
                }
            }
        }
        // Audio arrives every 100 ms, as it does during uninterrupted recording.
        let writer = Task {
            for offset in stride(from: 0, to: audio.count, by: 6400) {
                try Task.checkCancellation()
                try await client.sendAudio(audio.subdata(in: offset..<min(offset + 6400, audio.count)))
                try await Task.sleep(for: .milliseconds(realPort == nil ? 100 : 200))
            }
        }
        await fulfillment(of: [live], timeout: realPort == nil ? 3 : 20)
        writer.cancel()
        _ = try? await writer.value
        try await client.endAudio()
        await fulfillment(of: [final], timeout: 5)
        await client.disconnect()
        await reader.value
    }
}
