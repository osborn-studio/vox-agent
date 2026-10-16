import SwiftUI

// MARK: - Navigation Item

enum SettingsTab: String, CaseIterable, Identifiable {
    case general
    case language
    case models
    case vocabulary
    case agents
    case engine
    case modes
    case sync
    case history
    case about

    var id: String { rawValue }

    static var visibleTabs: [SettingsTab] {
        allCases
    }

    var displayName: String {
        switch self {
        case .general:     return L("通用", "General")
        case .language:    return L("语言", "Language")
        case .models:      return L("模型", "Models")
        case .vocabulary:  return L("词汇", "Vocabulary")
        case .agents:      return L("特工", "Agents")
        case .engine:      return L("引擎", "Engine")
        case .modes:       return L("模式", "Modes")
        case .sync:        return L("同步", "Sync")
        case .history:     return L("历史", "History")
        case .about:       return L("关于", "About")
        }
    }

    var subtitle: String {
        switch self {
        case .general:    return L("偏好与系统权限", "Preferences & permissions")
        case .language:   return L("中英文界面切换", "Chinese/English interface")
        case .models:      return L("语音识别与 LLM 引擎", "ASR & LLM engines")
        case .vocabulary:  return L("热词与片段替换", "Hotwords & snippets")
        case .agents:      return L("Agent 与小龙虾绑定", "Agents & OpenClaw binding")
        case .engine:      return L("任务引擎与工具", "Task engine & tools")
        case .modes:       return L("推理与默认行为", "Processing & defaults")
        case .sync:        return L("Git 增量跨设备同步", "Git incremental cross-device sync")
        case .history:     return L("会话与日志保留", "Sessions & logs")
        case .about:       return L("版本、许可证与支持", "Version, license & support")
        }
    }
}

// MARK: - Settings View

struct SettingsView: View {

    @Environment(AppState.self) private var appState
    @State private var selectedTab: SettingsTab = .general
    @AppStorage("tf_language") private var language = AppLanguage.systemDefault

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            content
        }
        .id(language)
        .frame(minWidth: 700, minHeight: 480)
        .background(TF.settingsBg)
        .preferredColorScheme(.light)
        .onReceive(NotificationCenter.default.publisher(for: .navigateToMode)) { note in
            selectedTab = .modes
            if let modeId = note.object as? UUID {
                NotificationCenter.default.post(name: .selectMode, object: modeId)
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .navigateToVocabulary)) { _ in
            selectedTab = .vocabulary
        }
        .onReceive(NotificationCenter.default.publisher(for: .navigateToAbout)) { _ in
            selectedTab = .about
            UpdateChecker.shared.markAsSeen(appState: appState)
        }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Brand
            VStack(alignment: .leading, spacing: 2) {
                Text("VOXLOG")
                    .font(.system(size: 11, weight: .bold))
                    .tracking(2)
                    .foregroundStyle(TF.settingsTextTertiary)
                Text(L("偏好设置", "Preferences"))
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(TF.settingsText)
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 16)

            // Nav items
            VStack(spacing: 2) {
                ForEach(SettingsTab.visibleTabs) { tab in
                    navItem(tab)
                }
            }
            .padding(.horizontal, 10)

            Spacer()
        }
        .frame(width: 180)
        .background(TF.settingsBg)
    }

    private func navItem(_ tab: SettingsTab) -> some View {
        let isActive = selectedTab == tab
        let showBadge = tab == .about && appState.hasUnseenUpdate
        return Button {
            selectedTab = tab
            if tab == .about {
                UpdateChecker.shared.markAsSeen(appState: appState)
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(tab.displayName)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(isActive ? .white : TF.settingsText)
                    Text(tab.subtitle)
                        .font(.system(size: 10))
                        .foregroundStyle(isActive ? .white.opacity(0.7) : TF.settingsTextTertiary)
                }
                Spacer()
                if showBadge {
                    Circle()
                        .fill(.red)
                        .frame(width: 7, height: 7)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(isActive ? TF.settingsNavActive : .clear)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Content

    private var content: some View {
        ZStack {
            tabPage(.general)    { GeneralSettingsTab() }
            tabPage(.language)   { LanguageSettingsTab() }
            tabPage(.models)     { ModelSettingsTab() }
            tabPage(.vocabulary) { VocabularyTab() }
            tabPage(.agents)     { AgentsSettingsTab() }
            tabPage(.engine)     { EngineSettingsTab() }
            tabPage(.modes)      { ModesSettingsTab() }
            tabPage(.sync)       { SyncSettingsTab() }
            fixedPage(.history)  { HistoryTab(isActive: selectedTab == .history) }
            tabPage(.about)      { AboutTab() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(TF.settingsCard)
    }

    /// Scrollable tab page (most tabs).
    private func tabPage<V: View>(_ tab: SettingsTab, @ViewBuilder content: () -> V) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                content()
            }
            .padding(28)
        }
        .opacity(selectedTab == tab ? 1 : 0)
        .allowsHitTesting(selectedTab == tab)
    }

    /// Fixed-height tab page (no outer scroll, content manages its own scroll).
    private func fixedPage<V: View>(_ tab: SettingsTab, @ViewBuilder content: () -> V) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .opacity(selectedTab == tab ? 1 : 0)
        .allowsHitTesting(selectedTab == tab)
    }
}

// MARK: - Reusable Components

struct SettingsSectionHeader: View {
    let label: String
    let title: String
    let description: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(TF.settingsTextTertiary)
            Text(title)
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(TF.settingsText)
            Text(description)
                .font(.system(size: 12))
                .foregroundStyle(TF.settingsTextTertiary)
                .lineSpacing(2)
        }
        .padding(.bottom, 16)
    }
}

struct SettingsRow: View {
    let label: String
    let value: String
    var statusColor: Color? = nil

    var body: some View {
        HStack {
            Text(label)
                .font(.system(size: 13))
                .foregroundStyle(TF.settingsText)
            Spacer()
            Text(value)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(statusColor ?? TF.settingsTextSecondary)
        }
        .padding(.vertical, 10)
    }
}

struct SettingsDivider: View {
    var body: some View {
        Divider().padding(.vertical, 2)
    }
}

struct LanguageSettingsTab: View {
    @AppStorage("tf_language") private var language = AppLanguage.systemDefault

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsSectionHeader(
                label: L("界面", "Interface"),
                title: L("语言", "Language"),
                description: L(
                    "语言设置会立即应用到登录页、设置向导、聊天工作台和偏好设置。它不会修改聊天记录、密钥或任何业务配置。",
                    "Language changes apply immediately to sign-in, setup, chat workspace, and preferences. They do not modify chat history, keys, or business settings."
                )
            )

            HStack {
                Text(L("显示语言", "Display language"))
                    .font(.system(size: 13))
                    .foregroundStyle(TF.settingsText)
                Spacer()
                LanguageSwitcher()
            }
            .padding(16)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(TF.settingsCardAlt)
            )

            SettingsRow(
                label: L("保存位置", "Stored in"),
                value: "UserDefaults: tf_language"
            )
            SettingsRow(
                label: L("数据保留", "Data retention"),
                value: L("不迁移历史和凭据", "History and credentials unchanged"),
                statusColor: TF.settingsAccentGreen
            )

            let _ = language
        }
    }
}
