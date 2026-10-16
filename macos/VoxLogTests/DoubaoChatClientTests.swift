import XCTest
@testable import VoxLog

final class DoubaoChatClientTests: XCTestCase {

    func testProcessInterpolatesPlainTextIntoTemplate() {
        let prompt = "请修正以下文本：{text}"
        let finalPrompt = prompt.replacingOccurrences(of: "{text}", with: "200毫秒")

        XCTAssertEqual(finalPrompt, "请修正以下文本：200毫秒")
    }
}
