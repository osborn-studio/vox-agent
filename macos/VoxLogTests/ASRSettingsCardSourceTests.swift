import XCTest

final class ASRSettingsCardSourceTests: XCTestCase {
    func testLocalQwen3SectionDoesNotShowLegacySenseVoiceRow() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Settings/ASRSettingsCard.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertFalse(source.contains("Text(\"SenseVoice\")"))
        XCTAssertFalse(source.contains("精准校验，建议搭配 SenseVoice"))
    }
}
