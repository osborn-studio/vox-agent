import Foundation

/// A local preference: changing devices never selects a repository on another Mac.
enum ChatSubmissionSettings {
    static let executionKey = "vox_chat_execute_agent"
    static let repositoryKey = "vox_chat_execution_repository"

    static func repository(defaults: UserDefaults = .standard) throws -> String? {
        guard defaults.bool(forKey: executionKey) else { return nil }
        let path = defaults.string(forKey: repositoryKey) ?? ""
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath()
        var directory: ObjCBool = false
        guard path.hasPrefix("/"),
              FileManager.default.fileExists(atPath: url.path, isDirectory: &directory), directory.boolValue,
              FileManager.default.fileExists(atPath: url.appendingPathComponent(".git").path),
              !url.pathComponents.contains(where: { $0.hasSuffix(".app") }) else {
            throw SelectionError.repositoryRequired
        }
        return url.path
    }

    enum SelectionError: LocalizedError {
        case repositoryRequired
        var errorDescription: String? {
            "请在设置 → 同步中选择代码仓库，或关闭“交给 Agent 执行”。"
        }
    }
}
