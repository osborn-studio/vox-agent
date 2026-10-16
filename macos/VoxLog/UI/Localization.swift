import Foundation

enum AppLanguage: String, CaseIterable {
    case zh
    case en

    var displayName: String {
        switch self {
        case .zh: return "中文"
        case .en: return "English"
        }
    }

    /// System language is Chinese? Default to zh, otherwise en.
    static var systemDefault: String {
        let preferred = Locale.preferredLanguages.first ?? "en"
        return preferred.hasPrefix("zh") ? "zh" : "en"
    }

    static var current: AppLanguage {
        AppLanguage(rawValue: UserDefaults.standard.string(forKey: "tf_language") ?? systemDefault) ?? .en
    }
}

/// Inline localization helper. Returns Chinese or English based on app language setting.
func L(_ zh: String, _ en: String) -> String {
    AppLanguage.current == .zh ? zh : en
}
