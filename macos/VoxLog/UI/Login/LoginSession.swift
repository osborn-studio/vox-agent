import Foundation

enum LoginMode: String {
    case localTeam = "local_team"
    case feishu = "feishu"
}

struct LoginSession {

    static let completedKey = "voxagent_hasCompletedLogin"
    static let loginModeKey = "voxagent_login_mode"
    static let activeOrgIdKey = "voxagent_active_org_id"
    static let activeOrgNameKey = "voxagent_active_org_name"
    static let defaultOrgId = "local-team"
    static let defaultOrgName = "Vox Agent"

    var hasCompletedLogin: Bool
    var mode: LoginMode
    var activeOrgId: String
    var activeOrgName: String

    static func load(from defaults: UserDefaults = .standard) -> LoginSession {
        let rawMode = defaults.string(forKey: loginModeKey)
        return LoginSession(
            hasCompletedLogin: defaults.bool(forKey: completedKey),
            mode: LoginMode(rawValue: rawMode ?? "") ?? .localTeam,
            activeOrgId: defaults.string(forKey: activeOrgIdKey) ?? defaultOrgId,
            activeOrgName: defaults.string(forKey: activeOrgNameKey) ?? defaultOrgName
        )
    }

    static func completeLocalTeamLogin(in defaults: UserDefaults = .standard) {
        defaults.set(true, forKey: completedKey)
        defaults.set(LoginMode.localTeam.rawValue, forKey: loginModeKey)
        defaults.set(defaultOrgId, forKey: activeOrgIdKey)
        defaults.set(defaultOrgName, forKey: activeOrgNameKey)
    }

    static func completeFeishuLogin(orgID: String, name: String, in defaults: UserDefaults = .standard) {
        defaults.set(true, forKey: completedKey)
        defaults.set(LoginMode.feishu.rawValue, forKey: loginModeKey)
        defaults.set(orgID, forKey: activeOrgIdKey)
        defaults.set(name, forKey: activeOrgNameKey)
    }

    static func resetLogin(in defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: completedKey)
        defaults.removeObject(forKey: loginModeKey)
        defaults.removeObject(forKey: activeOrgIdKey)
        defaults.removeObject(forKey: activeOrgNameKey)
    }
}
