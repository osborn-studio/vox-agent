import SwiftUI

// MARK: - Appearance Helper

extension NSAppearance {
    var isDark: Bool {
        bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }
}

// MARK: - Adaptive Color Helper

private func adaptiveColor(
    light: (r: CGFloat, g: CGFloat, b: CGFloat),
    dark: (r: CGFloat, g: CGFloat, b: CGFloat)
) -> Color {
    Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
        if appearance.isDark {
            return NSColor(srgbRed: dark.r, green: dark.g, blue: dark.b, alpha: 1.0)
        }
        return NSColor(srgbRed: light.r, green: light.g, blue: light.b, alpha: 1.0)
    }))
}

// MARK: - Design Tokens

enum TF {

    // MARK: Colors

    static let appBackground = adaptiveColor(
        light: (0.96, 0.97, 0.98),
        dark:  (0.09, 0.10, 0.11)
    )

    static let surface = adaptiveColor(
        light: (1.00, 1.00, 1.00),
        dark:  (0.13, 0.14, 0.15)
    )

    static let surfaceRaised = adaptiveColor(
        light: (0.92, 0.95, 0.97),
        dark:  (0.18, 0.19, 0.20)
    )

    static let line = adaptiveColor(
        light: (0.83, 0.86, 0.89),
        dark:  (0.25, 0.26, 0.28)
    )

    static let accent = adaptiveColor(
        light: (0.18, 0.43, 0.72),
        dark:  (0.32, 0.58, 0.86)
    )

    /// Warm amber accent: the signature "indicator light" color
    static let amber = adaptiveColor(
        light: (0.76, 0.49, 0.16),
        dark:  (0.83, 0.57, 0.24)
    )

    /// Recording active: warm red-orange, urgent but not alarming
    static let recording = adaptiveColor(
        light: (0.84, 0.34, 0.27),
        dark:  (0.87, 0.38, 0.30)
    )

    /// Success: muted warm green
    static let success = adaptiveColor(
        light: (0.35, 0.65, 0.35),
        dark:  (0.42, 0.70, 0.42)
    )

    /// Awaiting approval: a human action is required.
    static let warning = adaptiveColor(
        light: (0.85, 0.52, 0.04),
        dark:  (0.91, 0.58, 0.10)
    )

    /// Failed, disconnected, or destructive.
    static let danger = adaptiveColor(
        light: (0.85, 0.25, 0.28),
        dark:  (0.90, 0.28, 0.30)
    )

    /// Idle, archived, or disabled.
    static let idle = adaptiveColor(
        light: (0.68, 0.68, 0.66),
        dark:  (0.42, 0.42, 0.44)
    )

    enum Status: String, CaseIterable, Equatable, Sendable {
        case running
        case awaitingApproval
        case failed
        case idle
        case completed

        var color: Color {
            switch self {
            case .running: return TF.accent
            case .awaitingApproval: return TF.warning
            case .failed: return TF.danger
            case .idle: return TF.idle
            case .completed: return TF.success
            }
        }

        var symbolName: String {
            switch self {
            case .running: return "play.fill"
            case .awaitingApproval: return "exclamationmark"
            case .failed: return "xmark"
            case .idle: return "pause.fill"
            case .completed: return "checkmark"
            }
        }

        var label: String {
            switch self {
            case .running: return L("运行中", "Running")
            case .awaitingApproval: return L("等待批准", "Awaiting approval")
            case .failed: return L("失败", "Failed")
            case .idle: return L("空闲", "Idle")
            case .completed: return L("已完成", "Completed")
            }
        }
    }

    // MARK: Settings Palette

    static let settingsBg = Color(red: 0.95, green: 0.92, blue: 0.88)
    static let settingsCard = Color(red: 0.98, green: 0.96, blue: 0.93)
    static let settingsCardAlt = Color(red: 0.91, green: 0.89, blue: 0.85)
    static let settingsNavActive = Color(red: 0.10, green: 0.10, blue: 0.10)
    static let settingsText = Color(red: 0.10, green: 0.10, blue: 0.10)
    static let settingsTextSecondary = Color(red: 0.24, green: 0.24, blue: 0.24)
    static let settingsTextTertiary = Color(red: 0.42, green: 0.42, blue: 0.42)
    static let settingsAccentGreen = Color(red: 0.30, green: 0.62, blue: 0.35)
    static let settingsAccentAmber = Color(red: 0.78, green: 0.55, blue: 0.15)
    static let settingsAccentRed = Color(red: 0.80, green: 0.28, blue: 0.22)
    static let settingsAccentBlue = Color(red: 0.20, green: 0.45, blue: 0.75)

    // MARK: Spacing

    static let spacingXS: CGFloat = 4
    static let spacingSM: CGFloat = 8
    static let spacingMD: CGFloat = 12
    static let spacingLG: CGFloat = 16
    static let spacingXL: CGFloat = 24

    // MARK: Corner Radius

    static let cornerSM: CGFloat = 6
    static let cornerMD: CGFloat = 10
    static let cornerLG: CGFloat = 16

    // MARK: Floating Bar

    static let barWidth: CGFloat = 400
    static let barWidthCompact: CGFloat = 200
    static let barHeight: CGFloat = 52
    static let barBottomOffset: CGFloat = 48

    // MARK: Animation

    static let springSnappy = Animation.spring(response: 0.35, dampingFraction: 0.8)
    static let springGentle = Animation.spring(response: 0.5, dampingFraction: 0.75)
    static let springBouncy = Animation.spring(response: 0.4, dampingFraction: 0.65)
    static let easeQuick = Animation.easeOut(duration: 0.2)
    static let glassTint = Animation.easeInOut(duration: 0.5)
}
