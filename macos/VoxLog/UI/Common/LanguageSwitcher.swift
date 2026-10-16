import SwiftUI

struct LanguageSwitcher: View {

    var compact = false
    @AppStorage("tf_language") private var language = AppLanguage.systemDefault

    var body: some View {
        Picker(L("语言", "Language"), selection: $language) {
            ForEach(AppLanguage.allCases, id: \.rawValue) { item in
                Text(compact ? item.shortName : item.displayName).tag(item.rawValue)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: compact ? 112 : 176)
        .help(L("切换显示语言", "Switch display language"))
    }
}

private extension AppLanguage {
    var shortName: String {
        switch self {
        case .zh: return "中"
        case .en: return "EN"
        }
    }
}
