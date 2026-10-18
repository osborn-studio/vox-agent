import Foundation

enum ChatLayoutMetrics {
    static let privateInputEditorHeight: CGFloat = 96
    static let privateInboxMaxHeight: CGFloat = 360
    static let weeklyReviewMaxHeight: CGFloat = 280
    static let weeklyDraftEditorHeight: CGFloat = 120
    static let leftSidebarMinWidth: CGFloat = 240
    static let leftSidebarExpandedWidth: CGFloat = 260
    static let inspectorMinWidth: CGFloat = 300
    static let inspectorExpandedWidth: CGFloat = 340
    static let conversationMinWidth: CGFloat = 480
    static let windowStableMinWidth: CGFloat = 520
    static let conversationToolbarHeight: CGFloat = 46
    static let statusStripHeight: CGFloat = 30
    static let conversationContentMaxWidth: CGFloat = 660
    static let conversationContentHorizontalInset: CGFloat = 5
    static let timelineTurnSpacing: CGFloat = 20
    static let inspectorRowHeight: CGFloat = 28
    static let inputFontSize: CGFloat = 15
    static let messageFontSize: CGFloat = 15
    static let metadataFontSize: CGFloat = 11
    static let sidebarToggleButtonSize: CGFloat = 32
    static let scrollContentTrailingInset: CGFloat = 0
    static let scrollToBottomButtonSize: CGFloat = 36
    static let inputHorizontalInset: CGFloat = 12
    /// Breathing room above and below the composer surface.
    static let inputVerticalInset: CGFloat = 10
    static let inputMinHeight: CGFloat = 52
    /// Composer at rest: one text line plus the control row below it.
    static let inputRestHeight: CGFloat = inputTextMinHeight + inputToolbarHeight
    static let voiceWaveformHeight: CGFloat = 28
    /// Height of the voice header shown above the text while capturing.
    static let voiceHeaderHeight: CGFloat = 44
    /// Countdown ring drawn around the mic button during the silence countdown.
    static let voiceCountdownRingSize: CGFloat = 36
    /// Text area bounds. The composer grows upward with its content between
    /// these two values; beyond the maximum the text scrolls.
    static let inputTextMinHeight: CGFloat = 36
    static let inputMaxHeight: CGFloat = 176
    /// Text container insets; the placeholder uses the same values so it
    /// sits exactly where typed text starts.
    static let inputTextInsetHorizontal: CGFloat = 10
    static let inputTextInsetVertical: CGFloat = TF.spacingSM
    /// Bottom control row: Translate on the left; model badge, mic and send on the right.
    static let inputToolbarHeight: CGFloat = 36
    static let inputToolbarTrailingInset: CGFloat = 10
    /// Translate chip and model badge height inside the control row.
    static let inputChipHeight: CGFloat = 26
    /// Circular mic and send buttons.
    static let inputIconButtonSize: CGFloat = 30
    static let inputControlSpacing: CGFloat = 6
    static let inputModelBadgeMaxWidth: CGFloat = 220

    static func windowMinWidth(leftSidebarVisible: Bool, inspectorVisible: Bool) -> CGFloat {
        max(
            windowStableMinWidth,
            conversationMinWidth
            + (leftSidebarVisible ? leftSidebarMinWidth : 0)
            + (inspectorVisible ? inspectorMinWidth : 0)
        )
    }
}
