import XCTest
@testable import VoxLog

final class ChatLayoutMetricsTests: XCTestCase {

    func testInputAllowsAtLeastEightVisibleLines() {
        XCTAssertGreaterThanOrEqual(ChatLayoutMetrics.inputMaxVisibleLines, 8)
    }

    func testChatWindowDoesNotShrinkBelowStableComposerWidth() {
        XCTAssertEqual(ChatLayoutMetrics.windowStableMinWidth, 520)
        XCTAssertEqual(ChatLayoutMetrics.inputHorizontalInset, 12)
        XCTAssertEqual(
            ChatLayoutMetrics.windowMinWidth(leftSidebarVisible: false, inspectorVisible: false),
            ChatLayoutMetrics.windowStableMinWidth
        )
    }

    func testExpandedSidebarsContributeToWindowMinimumWidth() {
        XCTAssertEqual(ChatLayoutMetrics.leftSidebarMinWidth, 240)
        XCTAssertEqual(ChatLayoutMetrics.conversationMinWidth, 480)
        XCTAssertEqual(ChatLayoutMetrics.inspectorMinWidth, 300)
        XCTAssertEqual(
            ChatLayoutMetrics.windowMinWidth(leftSidebarVisible: true, inspectorVisible: true),
            max(
                ChatLayoutMetrics.windowStableMinWidth,
                ChatLayoutMetrics.leftSidebarMinWidth
                + ChatLayoutMetrics.conversationMinWidth
                + ChatLayoutMetrics.inspectorMinWidth
            )
        )
        XCTAssertEqual(
            ChatLayoutMetrics.windowMinWidth(leftSidebarVisible: false, inspectorVisible: false),
            ChatLayoutMetrics.windowStableMinWidth
        )
    }

    func testScrollbarInsetStaysTight() {
        XCTAssertLessThanOrEqual(ChatLayoutMetrics.scrollContentTrailingInset, 2)
    }

    func testCodexInspiredChatMetricsStayCompact() {
        XCTAssertEqual(ChatLayoutMetrics.conversationToolbarHeight, 46)
        XCTAssertEqual(ChatLayoutMetrics.statusStripHeight, 30)
        XCTAssertEqual(ChatLayoutMetrics.conversationContentMaxWidth, 660)
        XCTAssertEqual(ChatLayoutMetrics.conversationContentHorizontalInset, 5)
        XCTAssertEqual(ChatLayoutMetrics.timelineTurnSpacing, 20)
        XCTAssertLessThanOrEqual(ChatLayoutMetrics.sidebarToggleButtonSize, 32)
    }

    func testChatTextUsesReadableCodexScale() {
        XCTAssertGreaterThanOrEqual(ChatLayoutMetrics.messageFontSize, 15)
        XCTAssertGreaterThanOrEqual(ChatLayoutMetrics.inputFontSize, 15)
    }

    func testSidebarToggleButtonsHaveVisibleHitTargets() {
        XCTAssertGreaterThanOrEqual(ChatLayoutMetrics.sidebarToggleButtonSize, 32)
    }
}
