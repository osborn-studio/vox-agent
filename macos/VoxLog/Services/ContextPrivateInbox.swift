import Foundation
import CryptoKit
import Observation

struct ContextPrivateSource: Decodable, Equatable, Identifiable {
    let version: Int
    let id: String
    let eventId: String
    let revision: Int
    let visibility: String
    let evidenceKind: String
    let withdrawn: Bool
    let createdAt: String
    let revisionChangedAt: String
    let text: String?
    let tags: [String]?
    let projectId: String?
    let repositoryId: String?
    static let allowedTags = ["decision", "progress", "blocker", "question", "verification"]
    static func validText(_ value: String, limit: Int = 8192) -> Bool {
        !value.isEmpty && value.unicodeScalars.count <= limit && !value.contains("\0")
    }
    static func decode(_ data: Data) throws -> Self {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw ContextPrivateInbox.Failure.invalid }
        try validateKeys(object)
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.valid else { throw ContextPrivateInbox.Failure.invalid }
        return value
    }
    static func validateKeys(_ object: [String: Any]) throws {
        let base: Set<String> = ["version", "id", "eventId", "revision", "visibility", "evidenceKind", "withdrawn", "createdAt", "revisionChangedAt"]
        let terminal = (object["withdrawn"] as? Bool) == true
        guard Set(object.keys) == (terminal ? base : base.union(["text", "tags", "projectId", "repositoryId"])) else { throw ContextPrivateInbox.Failure.invalid }
    }
    var valid: Bool {
        guard version == 1, visibility == "private", evidenceKind == "user-statement", (1...1_000_000).contains(revision),
              ContextWeeklySource.identifier(id, maximum: 128), ContextWeeklySource.identifier(eventId, maximum: 128),
              createdAt.count <= 80, revisionChangedAt.count <= 80,
              let created = ContextWeeklySource.timestamp(createdAt), let changed = ContextWeeklySource.timestamp(revisionChangedAt), changed >= created else { return false }
        if withdrawn { return text == nil && tags == nil && projectId == nil && repositoryId == nil }
        guard let text, Self.validText(text), let tags, tags.count <= 5,
              Set(tags).count == tags.count, tags.allSatisfy(Self.allowedTags.contains),
              (projectId == nil) == (repositoryId == nil) else { return false }
        guard let projectId, let repositoryId else { return true }
        return ContextWeeklySource.identifier(projectId, maximum: 80)
            && repositoryId.utf8.count <= 200
            && repositoryId.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"#, options: .regularExpression) != nil
            && !repositoryId.contains(where: { $0.isWhitespace })
            && !repositoryId.components(separatedBy: "/").contains(where: { $0 == "." || $0 == ".." })
    }
}

/// Volatile personal consumer. No chat/history/backup persistence or publication.
@MainActor @Observable final class ContextPrivateInbox {
    enum Failure: Error { case invalid, durability }
    typealias Request = (String, String, Data?) async throws -> Data
    private(set) var sources: [ContextPrivateSource] = []
    private(set) var busy = false
    private(set) var writeInFlight = false
    private(set) var inputEditorEpoch = 0
    var draftEditorEpoch: Int { draftEpoch }
    private(set) var draftTooLarge = false
    private(set) var queryLimit = false
    private(set) var failed = false
    private(set) var writeUncertain = false
    private(set) var draftValid = false
    private(set) var hasDraft = false
    var draft = ""
    var input = "" { didSet { if oldValue != input { inputValueEpoch += 1; freezeInputProject() } } }
    private(set) var inputLimit = false
    private(set) var draftLimit = false
    var inputTag = "" { didSet { if oldValue != inputTag { inputValueEpoch += 1; freezeInputProject() } } }
    private(set) var projects: [InputBackupProject] = []
    private var catalogScope: String?
    private(set) var selectedProject: InputBackupProjectSnapshot?
    private(set) var inputAttribution = ContextProjectDraft()
    private(set) var projectStatus = ""
    var canChooseProject: Bool { !busy && !writeInFlight && !writeUncertain && !inputAttribution.frozen && editing == nil && emptyInputFrame }
    private func freezeInputProject() {
        if !input.isEmpty || !inputTag.isEmpty { inputAttribution.begin(project: selectedProject, accountScope: identity) }
    }
    private func resetInputProject(clearCatalog: Bool = false) {
        selectedProject = nil; inputAttribution.reset()
        if clearCatalog { projects = []; catalogScope = nil; projectStatus = "" }
    }
    func chooseProject(_ id: String, scope: () throws -> String) {
        guard canChooseProject else { return }
        do {
            let current = try scope()
            guard identity == nil || identity == current else { throw Failure.invalid }
            let project = projects.first(where: { $0.id == id })
            guard id.isEmpty || (catalogScope == current && project != nil) else { throw Failure.invalid }
            identity = current; selectedProject = project?.snapshot
        } catch { projects = []; catalogScope = nil; projectStatus = "unavailable" }
    }
    func setInput(_ value: String, scope: () throws -> String) {
        do {
            let current = try scope()
            guard identity == nil || identity == current else { throw Failure.invalid }
            identity = current; setInput(value)
        } catch { failed = true }
    }
    func setInputTag(_ value: String, scope: () throws -> String) {
        do {
            let current = try scope()
            guard identity == nil || identity == current else { throw Failure.invalid }
            identity = current; inputTag = value
        } catch { failed = true }
    }
    private func decodeProjects(_ data: Data) throws -> [InputBackupProject] {
        struct Catalog: Decodable { let version: Int; let projects: [InputBackupProject] }
        guard data.count <= 1_000_000, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["version", "projects"]),
              let rows = object["projects"] as? [[String: Any]], rows.count <= 1000,
              rows.allSatisfy({ Set($0.keys) == Set(["id", "name", "repositoryId"]) }),
              let catalog = try? JSONDecoder().decode(Catalog.self, from: data), catalog.version == 1,
              catalog.projects.allSatisfy({ $0.isValid && !$0.repositoryId.contains(where: { $0.isWhitespace }) }),
              Set(catalog.projects.map(\.id)).count == catalog.projects.count else { throw Failure.invalid }
        return catalog.projects
    }
    func loadProjects(request: Request, scope: () throws -> String) async {
        guard canChooseProject else { return }
        let token = generation, epoch = inputValueEpoch
        busy = true
        defer { if token == generation { busy = false } }
        do {
            let data = try await scoped("v1/context-projects", "GET", nil, request: request, scope: scope)
            let values = try decodeProjects(data)
            guard token == generation else { return }
            guard epoch == inputValueEpoch, !inputAttribution.frozen else { projectStatus = "stale"; return }
            catalogScope = try scope(); projects = values
            if let selectedProject, !values.contains(where: { $0.snapshot == selectedProject }) { self.selectedProject = nil }
            projectStatus = "loaded"
        } catch { if token == generation { projects = []; catalogScope = nil; if !inputAttribution.frozen { selectedProject = nil }; projectStatus = "unavailable" } }
    }
    private func verifyNewProject(_ project: InputBackupProjectSnapshot?, request: Request, scope: () throws -> String) async throws {
        let data = try await scoped("v1/context-private-authorization", "GET", nil, request: request, scope: scope)
        try validateInputAuthorization(data)
        if let project {
            let data = try await scoped("v1/context-projects", "GET", nil, request: request, scope: scope)
            guard try decodeProjects(data).contains(where: { $0.snapshot == project }) else { throw Failure.invalid }
        }
    }
    var query = ""
    var filterTag = ""
    struct WithdrawalReference: Codable, Equatable { let id: String; let eventId: String; let revision: Int; let createdAt: String; let changedAt: String }
    var withdrawalOutsideLoadedPage: Bool { withdrawalReference.map { ref in !sources.contains(where: { $0.id == ref.id && $0.revision == ref.revision }) } ?? false }
    private(set) var withdrawalReference: WithdrawalReference?
    struct PendingWrite: Codable, Equatable {
        let path: String; let method: String; let data: Data
        let mutationId: String?
        let selected: WithdrawalReference?; let withdrawn: Bool
        let eventId: String; let text: String?; let tags: [String]?
        let projectId: String?; let repositoryId: String?; let inputEditorId: String?
        init(path: String, method: String, data: Data, mutationId: String?, selected: WithdrawalReference?, withdrawn: Bool,
             eventId: String, text: String?, tags: [String]?, projectId: String?, repositoryId: String?, inputEditorId: String? = nil) {
            self.path = path; self.method = method; self.data = data; self.mutationId = mutationId; self.selected = selected; self.withdrawn = withdrawn
            self.eventId = eventId; self.text = text; self.tags = tags; self.projectId = projectId; self.repositoryId = repositoryId; self.inputEditorId = inputEditorId
        }
    }
    private struct UnsubmittedRecovery: Equatable { let text: String; let tag: String; let scope: String?; let reference: WithdrawalReference?; let editorId: String; let project: InputBackupProjectSnapshot? }
    private var unsubmittedRecovery: UnsubmittedRecovery?
    var hasUnsubmittedRecovery: Bool { unsubmittedRecovery != nil }
    var canRecoverUnsubmitted: Bool {
        hasUnsubmittedRecovery && !busy && !writeInFlight && !writeUncertain && !hasDraft
            && input.isEmpty && inputTag.isEmpty && editing == nil && pendingWrite == nil && stagedRequest == nil
    }
    private(set) var serverReplyStatus: Int?
    private(set) var serverReplyCode: String?
    private(set) var archiveReview: ContextPrivateRequestStore.ArchiveReview?
    private var archiveReviewGeneration: Int?
    private(set) var archiveUndo: ContextPrivateRequestStore.ArchiveReview?
    private(set) var archiveFiles: [ContextPrivateRequestStore.ArchiveFileInfo] = []
    private(set) var archiveFilesStatus = ""
    private var archiveFilesScope: String?
    func archiveFilesAreCurrent(scope: () throws -> String) -> Bool {
        guard let owner = archiveFilesScope, let current = try? scope() else { return false }
        return owner == current
    }
    func browseArchiveFiles(scope: () throws -> String) {
        archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]; archiveKeyStatus = nil; archiveFingerprintStatus = [:]; archiveFiles = []; archiveFilesStatus = ""; archiveFilesScope = nil
        guard !busy, !writeInFlight else { return }
        guard let requestStore else { archiveFilesStatus = "unavailable"; return }
        do {
            let owner = try scope()
            guard identity == nil || identity == owner else { throw Failure.invalid }
            let files = try requestStore.listArchiveFiles(scope: owner)
            guard try scope() == owner else { throw Failure.invalid }
            archiveFilesScope = owner; archiveFiles = files; archiveFilesStatus = "loaded"
        } catch ContextPrivateRequestStore.Failure.capacity { archiveFilesStatus = "capacity" }
        catch { archiveFilesStatus = "unavailable" }
    }
    private(set) var archiveKeyStatus: ContextPrivateRequestStore.ArchiveKeyState?
    func inspectArchiveKey(scope: () throws -> String) async {
        archiveKeyStatus = nil
        let token = generation
        guard !busy, !writeInFlight, !archiveInspectionInFlight, let requestStore else { return }
        archiveInspectionInFlight = true; defer { archiveInspectionInFlight = false }
        let inspectionToken = archiveAuthenticationToken
        do {
            let owner = try scope()
            guard archiveFilesScope == owner, !archiveFiles.isEmpty, identity == nil || identity == owner else { throw Failure.invalid }
            let result = await Task.detached(priority: .utility) { requestStore.inspectArchiveKey(scope: owner) }.value
            guard archiveAuthenticationToken == inspectionToken else { return }
            guard generation == token, archiveFilesScope == owner, try scope() == owner else { throw Failure.invalid }
            archiveKeyStatus = result
        } catch { archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]; archiveKeyStatus = nil; archiveFingerprintStatus = [:]; archiveFiles = []; archiveFilesStatus = "unavailable"; archiveFilesScope = nil }
    }
    private(set) var archiveFingerprintStatus: [String: String] = [:]
    func inspectArchiveFingerprint(label: String, scope: () throws -> String) async {
        guard !busy, !writeInFlight, !archiveInspectionInFlight, let requestStore else { return }
        archiveInspectionInFlight = true; defer { archiveInspectionInFlight = false }
        let inspectionToken = archiveAuthenticationToken
        do {
            let owner = try scope()
            guard archiveFilesScope == owner, archiveFiles.contains(where: { $0.id == label }),
                  identity == nil || identity == owner else { throw Failure.invalid }
            let result = await Task.detached(priority: .utility) { try? requestStore.archiveFingerprintMatches(scope: owner, label: label) }.value
            guard archiveAuthenticationToken == inspectionToken else { return }
            guard let matches = result else { throw Failure.invalid }
            guard try scope() == owner else { throw Failure.invalid }
            archiveFingerprintStatus[label] = matches ? "matches" : "mismatch"
        } catch { archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]; archiveKeyStatus = nil; archiveFingerprintStatus.removeAll(); archiveFiles = []; archiveFilesStatus = "unavailable"; archiveFilesScope = nil }
    }
    private(set) var archiveInspectionInFlight = false // Survives reset until owned background work actually ends.
    private var archiveAuthenticationToken = UUID()
    private(set) var archiveAuthenticationStatus: [String: String] = [:]
    func authenticateArchive(label: String, scope: () throws -> String) async {
        guard !busy, !writeInFlight, !archiveInspectionInFlight, let requestStore,
              archiveAuthenticationStatus[label] != "checking" else { return }
        guard let owner = try? scope(), archiveFilesScope == owner,
              archiveFiles.contains(where: { $0.id == label }), identity == nil || identity == owner else { return }
        archiveInspectionInFlight = true; defer { archiveInspectionInFlight = false }
        let token = archiveAuthenticationToken, epoch = generation
        archiveAuthenticationStatus[label] = "checking"
        let authenticated = await Task.detached(priority: .utility) {
            (try? requestStore.inspectAuthenticatedArchive(scope: owner, label: label)) != nil
        }.value
        guard archiveAuthenticationToken == token else { return }
        guard generation == epoch else { archiveAuthenticationStatus.removeValue(forKey: label); return }
        guard archiveFilesScope == owner, identity == nil || identity == owner, (try? scope()) == owner else {
            archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]
            archiveFiles = []; archiveFilesScope = nil; archiveFilesStatus = "unavailable"
            return
        }
        archiveAuthenticationStatus[label] = !Task.isCancelled && authenticated ? "authenticated" : "unavailable"
    }
    private var archivablePending: PendingWrite? { pendingWrite ?? stagedRequest?.pending }
    var uncertainProjectInput: Bool { archivablePending.map { $0.selected == nil && $0.projectId != nil } ?? false }
    var canReviewArchive: Bool {
        !busy && !writeInFlight && !hasUnsubmittedRecovery && !hasDraft && requestStore != nil
            && archivablePending.map { input.isEmpty || input == $0.text } == true
    }
    private var pendingWrite: PendingWrite?
    private var pendingScope: String?
    private let requestStore: ContextPrivateRequestStore?
    private let inputStore: ContextPrivateInputStore?
    private var stagedInput: ContextPrivateSavedInput?
    var hasStagedInput: Bool { stagedInput != nil }
    private(set) var inputSavedStatus = ""
    private var inputValueEpoch = 0
    private var inputLineage = UUID().uuidString.lowercased()
    init(requestStore: ContextPrivateRequestStore? = nil, inputStore: ContextPrivateInputStore? = nil) { self.requestStore = requestStore; self.inputStore = inputStore }
    private var stagedRequest: ContextPrivateSavedRequest?
    var hasStagedRequest: Bool { stagedRequest != nil }
    var canStageSavedRequest: Bool { !busy && !writeInFlight && input.isEmpty && !hasDraft && !hasUnsubmittedRecovery && pendingWrite == nil }
    private(set) var requestRecoveryStatus = ""
    private(set) var editing: ContextPrivateSource?
    private(set) var hasMore = false
    private(set) var omitted = false
    private(set) var pages = 0
    private var cursor: String?
    private var visited: Set<String> = []
    private var identity: String?
    private var generation = 0
    private var draftEpoch = 0
    private var stagedDraft: ContextPrivateSavedDraft?
    var hasStagedDraft: Bool { stagedDraft != nil }
    private(set) var savedStatus = ""
    private var refs: [(String, Int)] = []
    private var window: (String, String)?
    private var searchSnapshot: (String, String)?
    private func clearList() {
        sources = []; pages = 0; hasMore = false; omitted = false; cursor = nil; visited = []; window = nil; searchSnapshot = nil
    }
    func invalidateDraft() { draftEpoch += 1; draftLimit = false; draftTooLarge = false; draft = ""; refs = []; draftValid = false; hasDraft = false }
    /// First display identity may arrive after input captures a scope. Preserve only the same owner.
    @discardableResult
    func reconcileDisplayIdentity(previous: String?, current: String?) -> Bool {
        let capturedOwners = [identity, pendingScope, catalogScope, inputAttribution.accountScope, stagedRequest?.scope, stagedInput?.scope, stagedDraft?.scope,
                              archiveReview?.scope, archiveUndo?.scope, archiveFilesScope, unsubmittedRecovery?.scope]
            .compactMap { $0 }
        guard previous != nil || current == nil || capturedOwners.contains(where: { $0 != current }) else { return false }
        reset()
        return true
    }
    func reset() {
        archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]; archiveKeyStatus = nil; archiveFingerprintStatus = [:]; archiveFiles = []; archiveFilesStatus = ""; archiveFilesScope = nil
        resetInputProject(clearCatalog: true)
        stagedInput = nil; inputSavedStatus = ""; inputLineage = UUID().uuidString.lowercased()
        serverReplyStatus = nil; serverReplyCode = nil
        archiveReview = nil; archiveReviewGeneration = nil; archiveUndo = nil
        generation += 1; inputEditorEpoch += 1; sources = []; input = ""; editing = nil; query = ""; inputTag = ""; filterTag = ""
        identity = nil; pendingScope = nil; stagedRequest = nil; requestRecoveryStatus = ""; stagedDraft = nil; savedStatus = ""; unsubmittedRecovery = nil; queryLimit = false; inputLimit = false; draftLimit = false; withdrawalReference = nil; pendingWrite = nil; busy = false; failed = false; writeUncertain = false
        cursor = nil; visited = []; pages = 0; hasMore = false; omitted = false; window = nil; searchSnapshot = nil
        invalidateDraft(); resetInputProject(clearCatalog: true)
    }
    var searchChanged: Bool { searchSnapshot.map { $0 != (query, filterTag) } ?? false }
    func suspend() {
        archiveAuthenticationToken = UUID(); archiveAuthenticationStatus = [:]; archiveKeyStatus = nil; archiveFingerprintStatus = [:]; archiveFiles = []; archiveFilesStatus = ""; archiveFilesScope = nil
        resetInputProject(clearCatalog: true)
        stagedInput = nil; inputSavedStatus = ""; inputLineage = UUID().uuidString.lowercased()
        serverReplyStatus = nil; serverReplyCode = nil
        archiveReview = nil; archiveReviewGeneration = nil; archiveUndo = nil
        stagedRequest = nil; requestRecoveryStatus = ""; stagedDraft = nil; savedStatus = ""
        generation += 1; inputEditorEpoch += 1; inputLimit = false; draftLimit = false; queryLimit = false; failed = false; busy = false; sources = []; editing = nil; input = ""; inputTag = ""
        pages = 0; hasMore = false; query = ""; filterTag = ""; searchSnapshot = nil; omitted = false; window = nil; invalidateDraft()
        // Keep the full immutable pending request (including private body) and minimal withdrawal metadata in memory for explicit same-scope recovery.
        if pendingWrite != nil {
            writeUncertain = true
            if requestStore != nil { pendingWrite = nil; pendingScope = nil; requestRecoveryStatus = "reload" }
        }
        resetInputProject(clearCatalog: true)
    }
    func setQuery(_ value: String) {
        queryLimit = value.unicodeScalars.count > 128
        if !queryLimit { query = value }
    }
    func setInput(_ value: String) {
        inputLimit = value.unicodeScalars.count > 8192
        if !inputLimit { input = value }
    }
    func setDraft(_ value: String) {
        guard !busy else { return }
        draftLimit = value.unicodeScalars.count > 300000
        if !draftLimit { draft = value }
    }
    func discardUncertain() { guard !busy, !writeInFlight else { return }; reset() }
    var canSaveInput: Bool {
        inputStore != nil && requestStore != nil && !busy && !writeInFlight && !writeUncertain && !hasDraft && !hasUnsubmittedRecovery
            && pendingWrite == nil && stagedRequest == nil && ContextPrivateSource.validText(input)
    }
    private var emptyInputFrame: Bool {
        !writeInFlight && !writeUncertain && !hasDraft && !hasUnsubmittedRecovery && pendingWrite == nil
            && stagedRequest == nil && input.isEmpty && inputTag.isEmpty && editing == nil && withdrawalReference == nil
    }
    var canStageInput: Bool { inputStore != nil && requestStore != nil && !busy && emptyInputFrame }
    func saveInputOffline(scope: () throws -> String) {
        guard canSaveInput, let inputStore else { return }
        let token = generation, epoch = inputValueEpoch
        do {
            let current = try scope()
            guard identity == current,
                  editing != nil || (inputAttribution.frozen && inputAttribution.accountScope == current) else { throw Failure.invalid }
            let ref = editing.map { WithdrawalReference(id: $0.id, eventId: $0.eventId, revision: $0.revision, createdAt: $0.createdAt, changedAt: $0.revisionChangedAt) }
            let attribution: (String, String)?
            if let editing { attribution = editing.projectId.flatMap { id in editing.repositoryId.map { (id, $0) } } }
            else { attribution = inputAttribution.project.map { ($0.id, $0.repositoryID) } }
            let record = ContextPrivateSavedInput(version: 1, scope: current, text: input, tag: inputTag,
                reference: ref, projectId: attribution?.0, repositoryId: attribution?.1,
                savedAt: ISO8601DateFormatter().string(from: max(Date(), ref.flatMap { ContextWeeklySource.timestamp($0.changedAt) } ?? Date())), editorId: inputLineage)
            try inputStore.save(record)
            guard token == generation, epoch == inputValueEpoch, try scope() == current else { return }
            inputSavedStatus = "saved"; stagedInput = nil
        } catch ContextPrivateInputStore.Failure.capacity {
            if token == generation { inputSavedStatus = "capacity" }
        } catch ContextPrivateInputStore.Failure.installed {
            if token == generation { inputSavedStatus = "durability" }
        } catch { if token == generation { inputSavedStatus = "unavailable" } }
    }
    func stageInputOffline(scope: () throws -> String) {
        guard canStageInput, let inputStore else { return }
        let token = generation
        do {
            let current = try scope()
            guard identity == nil || identity == current else { throw Failure.invalid }
            guard try requestStore?.hasUnconfirmed(scope: current) != true else { inputSavedStatus = "pending"; return }
            let record = try inputStore.loadIfPresent(scope: current)
            guard token == generation, try scope() == current, emptyInputFrame else { return }
            guard let record else { inputSavedStatus = "not-found"; return }
            guard record.boundRequestFingerprint == nil else { inputSavedStatus = "submitted"; return }
            stagedInput = record; inputSavedStatus = "hidden"
        } catch { if token == generation { stagedInput = nil; inputSavedStatus = "unavailable" } }
    }
    private struct InputAuthorization: Decodable { let version: Int; let allowed: Bool; let meaning: String }
    private func validateInputAuthorization(_ data: Data) throws {
        guard data.count <= 1024, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["version", "allowed", "meaning"]),
              let authorization = try? JSONDecoder().decode(InputAuthorization.self, from: data),
              authorization.version == 1, authorization.allowed,
              authorization.meaning == "current-private-intake-authorization" else { throw Failure.invalid }
    }
    func restoreInput(request: Request, scope: () throws -> String) async {
        guard !busy, emptyInputFrame, let record = stagedInput, let inputStore, requestStore != nil else { return }
        let token = generation, epoch = inputValueEpoch
        busy = true
        defer { if token == generation { busy = false } }
        do {
            let current = try scope()
            guard current == record.scope, record.boundRequestFingerprint == nil,
                  try requestStore?.hasUnconfirmed(scope: current) != true else { throw Failure.invalid }
            var source: ContextPrivateSource?
            if let ref = record.reference {
                let data = try await scoped("v1/context-private/" + ref.id, "GET", nil, request: request, scope: scope)
                let value = try ContextPrivateSource.decode(data)
                guard !value.withdrawn, value.id == ref.id, value.eventId == ref.eventId, value.revision == ref.revision,
                      value.createdAt == ref.createdAt, value.revisionChangedAt == ref.changedAt,
                      value.projectId == record.projectId, value.repositoryId == record.repositoryId else { throw Failure.invalid }
                source = value
            } else {
                let project = record.projectId.flatMap { id in record.repositoryId.map { InputBackupProjectSnapshot(id: id, repositoryID: $0) } }
                try await verifyNewProject(project, request: request, scope: scope)
            }
            guard token == generation, epoch == inputValueEpoch, emptyInputFrame, stagedInput == record,
                  try scope() == current, try requestStore?.hasUnconfirmed(scope: current) != true,
                  try inputStore.loadIfPresent(scope: current) == record else { throw Failure.invalid }
            inputEditorEpoch += 1; editing = source; withdrawalReference = record.reference; inputLineage = record.editorId
            resetInputProject(); selectedProject = record.projectId.flatMap { id in record.repositoryId.map { InputBackupProjectSnapshot(id: id, repositoryID: $0) } }
            inputAttribution.begin(project: selectedProject, accountScope: current)
            input = record.text; inputTag = record.tag; stagedInput = nil; inputSavedStatus = "restored"
        } catch { if token == generation { inputSavedStatus = "unavailable" } }
    }
    private func body(_ value: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
    private func scoped(_ path: String, _ method: String, _ body: Data?, request: Request, scope: () throws -> String) async throws -> Data {
        let current = try scope()
        guard identity == nil || identity == current else { throw Failure.invalid }
        identity = current
        let data = try await request(path, method, body)
        try Task.checkCancellation()
        guard try scope() == current, data.count <= 4_000_000 else { throw Failure.invalid }
        return data
    }
    func load(more: Bool = false, request: Request, scope: () throws -> String) async {
        guard !busy, !more || (hasMore && pages < 5 && !hasDraft) else { return }
        let token = generation; busy = true; failed = false
        defer { if token == generation { busy = false } }
        do {
            if !more {
                queryLimit = false; invalidateDraft(); sources = []; pages = 0; cursor = nil; visited = []; omitted = false
                let end = Date().addingTimeInterval(300)
                let format = ISO8601DateFormatter()
                window = (format.string(from: end.addingTimeInterval(-7 * 86400)), format.string(from: end))
                searchSnapshot = (query, filterTag)
            }
            guard let window, let windowStart = ContextWeeklySource.timestamp(window.0), let windowEnd = ContextWeeklySource.timestamp(window.1),
                  let searchSnapshot, searchSnapshot == (query, filterTag) else { throw Failure.invalid }
            var payload: [String: Any] = ["start": window.0, "end": window.1, "limit": 20]
            if !searchSnapshot.0.isEmpty {
                guard ContextPrivateSource.validText(searchSnapshot.0, limit: 128) else { throw Failure.invalid }
                payload["q"] = searchSnapshot.0
            }
            if !searchSnapshot.1.isEmpty { payload["tag"] = searchSnapshot.1 }
            if let cursor { payload["cursor"] = cursor }
            let data = try await scoped("v1/context-private-search", "POST", body(payload), request: request, scope: scope)
            guard token == generation else { return }
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  Set(object.keys) == Set(["version", "scope", "items", "hasMore", "nextCursor", "omittedRecords", "evidenceKind"]),
                  let items = object["items"] as? [[String: Any]], items.count <= 20 else { throw Failure.invalid }
            struct Page: Decodable { let version: Int; let scope: String; let evidenceKind: String; let hasMore: Bool; let nextCursor: String?; let omittedRecords: Bool }
            let page = try JSONDecoder().decode(Page.self, from: data)
            let values = try items.map { try ContextPrivateSource.decode(JSONSerialization.data(withJSONObject: $0)) }
            guard page.version == 1, page.scope == "own-private-context", page.evidenceKind == "user-statement",
                  values.count <= 20, values.allSatisfy({ !$0.withdrawn
                      && ContextWeeklySource.timestamp($0.createdAt)! >= windowStart
                      && ContextWeeklySource.timestamp($0.createdAt)! < windowEnd }), Set(values.map(\.id)).count == values.count,
                  Set(sources.map(\.id)).isDisjoint(with: values.map(\.id)), page.hasMore == (page.nextCursor != nil),
                  page.nextCursor.map({ ContextWeeklySource.identifier($0, maximum: 256) && !visited.contains($0) && $0 != cursor }) ?? true else { throw Failure.invalid }
            if let cursor { visited.insert(cursor) }
            sources += values; cursor = page.nextCursor; hasMore = page.hasMore; omitted = omitted || page.omittedRecords; pages += 1
            // Loading a list cannot prove that an ambiguous write did or did not commit.
            if !more {
                editing = nil; inputEditorEpoch += 1; inputLimit = false
                if !writeUncertain { input = ""; inputTag = "" }
            }
        } catch {
            guard token == generation else { return }
            clearList(); invalidateDraft(); inputEditorEpoch += 1; editing = nil; input = ""; inputTag = ""; failed = true
        }
    }
    func select(_ source: ContextPrivateSource) {
        guard !busy, !writeUncertain, sources.contains(source), !source.withdrawn else { return }
        resetInputProject()
        inputLineage = UUID().uuidString.lowercased()
        inputEditorEpoch += 1; inputLimit = false; editing = source; withdrawalReference = WithdrawalReference(id: source.id, eventId: source.eventId, revision: source.revision, createdAt: source.createdAt, changedAt: source.revisionChangedAt); input = source.text ?? ""; inputTag = source.tags?.first ?? ""
    }
    func newInput() { guard !busy, !writeUncertain else { return }; resetInputProject(); inputLineage = UUID().uuidString.lowercased(); stagedInput = nil; inputEditorEpoch += 1; inputLimit = false; unsubmittedRecovery = nil; editing = nil; withdrawalReference = nil; input = ""; inputTag = ""; resetInputProject() }
    func mutate(withdraw: Bool = false, request: Request, scope: () throws -> String) async {
        guard inputStore == nil || requestStore != nil else { failed = true; return }
        guard !busy, !writeInFlight, !writeUncertain, (withdraw || !hasUnsubmittedRecovery), (!withdraw || withdrawalReference != nil), withdraw || ContextPrivateSource.validText(input) else { return }
        serverReplyStatus = nil; serverReplyCode = nil
        let token = generation; busy = true; writeInFlight = true; failed = false; invalidateDraft()
        defer { writeInFlight = false; if token == generation { busy = false } }
        do {
            let currentScope = try scope()
            guard identity == nil || identity == currentScope, inputAttribution.accountScope == nil || inputAttribution.accountScope == currentScope else { throw Failure.invalid }
            let selected = editing
            if selected == nil, !withdraw {
                guard inputAttribution.frozen, inputAttribution.accountScope == currentScope else { throw Failure.invalid }
            }
            if selected == nil, !withdraw, let project = inputAttribution.project {
                guard inputAttribution.accountScope == currentScope else { throw Failure.invalid }
                let epoch = inputValueEpoch
                try await verifyNewProject(project, request: request, scope: scope)
                guard token == generation, epoch == inputValueEpoch, try scope() == currentScope,
                      inputAttribution.project == project else { throw Failure.invalid }
            }
            var payload: [String: Any] = [:]
            var path = "v1/context-private", method = "POST"
            let reference = withdraw ? withdrawalReference : selected.map { WithdrawalReference(id: $0.id, eventId: $0.eventId, revision: $0.revision, createdAt: $0.createdAt, changedAt: $0.revisionChangedAt) }
            if let reference {
                payload["expectedRevision"] = reference.revision
                payload["requestId"] = UUID().uuidString.lowercased()
                path += "/" + reference.id
                if withdraw { path += "/withdraw" } else { method = "PUT" }
            } else { payload["eventId"] = UUID().uuidString.lowercased(); payload["visibility"] = "private" }
            if reference == nil, let project = inputAttribution.project {
                payload["projectId"] = project.id; payload["expectedRepositoryId"] = project.repositoryID
            }
            if !withdraw {
                guard inputTag.isEmpty || ContextPrivateSource.allowedTags.contains(inputTag) else { throw Failure.invalid }
                payload["text"] = input; payload["tags"] = inputTag.isEmpty ? [] : [inputTag]
                // Preserve all existing tags when only the original first tag remains selected.
                if let selected, inputTag == (selected.tags?.first ?? "") { payload["tags"] = selected.tags ?? [] }
            }
            // Treat attribution as a pair; never fall back to a new-input picker for an edit/withdrawal.
            let attribution: (String, String)?
            if reference != nil {
                attribution = selected?.projectId.flatMap { id in selected?.repositoryId.map { (id, $0) } }
            } else { attribution = inputAttribution.project.map { ($0.id, $0.repositoryID) } }
            let pending = PendingWrite(path: path, method: method, data: try body(payload), mutationId: payload["requestId"] as? String, selected: reference, withdrawn: withdraw,
                                       eventId: reference?.eventId ?? (payload["eventId"] as! String),
                                       text: payload["text"] as? String, tags: payload["tags"] as? [String],
                                       projectId: attribution?.0, repositoryId: attribution?.1, inputEditorId: inputStore == nil ? nil : inputLineage)
            let originalScope = try scope()
            // Durable write before HTTP; a failure never installs a different in-memory request.
            do { try requestStore?.save(.init(version: 1, scope: originalScope, pending: pending, confirmed: false)) }
            catch ContextPrivateRequestStore.Failure.capacity {
                requestRecoveryStatus = "capacity"
                throw Failure.invalid // Refused before install; retain editor and do not dispatch HTTP.
            }
            catch ContextPrivateRequestStore.Failure.installed {
                pendingWrite = pending; pendingScope = originalScope; requestRecoveryStatus = "durability"
                throw Failure.durability // Ciphertext installed but durability unconfirmed; zero HTTP.
            }
            pendingWrite = pending; pendingScope = originalScope
            guard try scope() == originalScope else { throw Failure.invalid }
            try bindInput(pending)
            let data = try await scoped(path, method, pending.data, request: request, scope: scope)
            guard token == generation else { return }
            try acceptWrite(data, pending: pending)
        } catch {
            guard token == generation else { return }
            if let reply = error as? ContextPrivateHTTPFailure { serverReplyStatus = reply.status; serverReplyCode = reply.code }
            writeUncertain = pendingWrite != nil; failed = true; clearList(); editing = nil // Never silently replay an ambiguous write.
        }
    }
    private func matchesAcknowledgment(_ value: ContextPrivateSource, pending: PendingWrite) -> Bool {
        guard let changed = ContextWeeklySource.timestamp(value.revisionChangedAt),
              changed <= Date().addingTimeInterval(300),
              value.withdrawn == pending.withdrawn, value.eventId == pending.eventId,
              pending.selected.map({ value.id == $0.id && value.revision == $0.revision + 1 && value.createdAt == $0.createdAt
                  && ContextWeeklySource.timestamp($0.changedAt).map({ changed >= $0 }) == true })
                  ?? (value.revision == 1 && value.createdAt == value.revisionChangedAt),
              pending.withdrawn || (value.text == pending.text && Set(value.tags ?? []) == Set(pending.tags ?? [])
                 && value.projectId == pending.projectId && value.repositoryId == pending.repositoryId) else { return false }
        return true
    }
    private func bindInput(_ pending: PendingWrite) throws {
        if let inputStore, let editor = pending.inputEditorId {
            guard let pendingScope else { throw Failure.invalid }
            let hash = SHA256.hash(data: Data((pending.eventId + "|" + (pending.mutationId ?? "")).utf8)).map { String(format: "%02x", $0) }.joined()
            try inputStore.bindSubmission(scope: pendingScope, editorId: editor, fingerprint: hash)
        }
    }
    private func acceptWrite(_ data: Data, pending: PendingWrite) throws {
        let value = try ContextPrivateSource.decode(data)
        guard matchesAcknowledgment(value, pending: pending) else { throw Failure.invalid }
        try bindInput(pending)
        if let requestStore {
            guard let pendingScope else { throw Failure.invalid }
            do { try requestStore.save(.init(version: 1, scope: pendingScope, pending: pending, confirmed: true)) }
            catch ContextPrivateRequestStore.Failure.installed {
                requestRecoveryStatus = "durability"
                throw Failure.durability
            }
        }
        resetInputProject()
        unsubmittedRecovery = nil; stagedInput = nil; inputLineage = UUID().uuidString.lowercased()
        sources = []; pages = 0; hasMore = false; inputEditorEpoch += 1; inputLimit = false; editing = nil; input = ""; inputTag = ""
        resetInputProject()
        withdrawalReference = nil; pendingScope = nil; pendingWrite = nil; writeUncertain = false; failed = false; requestRecoveryStatus = "confirmed"
    }
    func stageSavedRequest(scope: () throws -> String) {
        guard canStageSavedRequest else { return }
        stagedRequest = nil; requestRecoveryStatus = ""
        do {
            guard let requestStore else { throw Failure.invalid }
            let current = try scope(), record = try requestStore.load(scope: current)
            guard !record.confirmed, record.scope == current else { throw Failure.invalid }
            clearList(); identity = nil; editing = nil; withdrawalReference = nil
            stagedRequest = record; requestRecoveryStatus = "staged"
        } catch { requestRecoveryStatus = "unavailable" }
    }
    func restoreSavedRequest(request: Request, scope: () throws -> String) async {
        guard !busy, !writeInFlight, pendingWrite == nil, input.isEmpty, !hasDraft, !hasUnsubmittedRecovery, let staged = stagedRequest else { return }
        do {
            guard staged.scope == (try scope()) else { throw Failure.invalid }
            pendingScope = staged.scope; pendingWrite = staged.pending; writeUncertain = true
            stagedRequest = nil
            await reconcileUncertain(request: request, scope: scope)
        } catch { stagedRequest = nil; requestRecoveryStatus = "unavailable" }
    }
    private func readBack(_ pending: PendingWrite, token: Int, request: Request, scope: () throws -> String) async throws -> Bool {
        guard pendingScope == (try scope()) else { throw Failure.invalid }
        let path = "v1/context-private-events/" + pending.eventId + (pending.mutationId.map { "/requests/" + $0 } ?? "")
        let data = try await scoped(path, "GET", nil, request: request, scope: scope)
        guard token == generation, pendingScope == (try scope()),
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["version", "meaning", "status", "source", "mutationId"]) else { throw Failure.invalid }
        struct Status: Decodable { let version: Int; let meaning: String; let status: String; let mutationId: String? }
        let status = try JSONDecoder().decode(Status.self, from: data)
        guard status.version == 1, status.meaning == "point-in-time-only-not-absence-proof" else { throw Failure.invalid }
        if object["source"] is NSNull {
            guard status.status == "not-observed", status.mutationId == nil else { throw Failure.invalid }
            if pending.selected != nil {
                guard pending.mutationId != nil else { throw Failure.invalid }
                let current = try await scoped("v1/context-private/" + pending.selected!.id, "GET", nil, request: request, scope: scope)
                guard token == generation, pendingScope == (try scope()) else { throw Failure.invalid }
                let source = try ContextPrivateSource.decode(current)
                guard let selected = pending.selected, !source.withdrawn, source.eventId == pending.eventId,
                      source.id == selected.id, source.revision == selected.revision,
                      source.createdAt == selected.createdAt, source.revisionChangedAt == selected.changedAt,
                      pending.withdrawn || (source.projectId == pending.projectId && source.repositoryId == pending.repositoryId) else { throw Failure.invalid }
            }
            return false // Never evidence of non-commit; retain the immutable request and reconcile again.
        }
        guard status.status == "observed", let raw = object["source"] as? [String: Any] else { throw Failure.invalid }
        let source = try ContextPrivateSource.decode(JSONSerialization.data(withJSONObject: raw))
        guard source.eventId == pending.eventId, status.mutationId == pending.mutationId else { throw Failure.invalid }
        if status.mutationId == pending.mutationId && matchesAcknowledgment(source, pending: pending) {
            try acceptWrite(try JSONSerialization.data(withJSONObject: raw), pending: pending)
            return true // A durable finalize failure throws, preserving unknown; never POST in this path.
        }
        // An observed nonce must prove the exact committed revision. A baseline
        // source under that nonce contradicts the receipt; never treat it as absence.
        throw Failure.invalid
    }
    func reviewArchive(scope: () throws -> String) {
        archiveReview = nil; archiveReviewGeneration = nil
        guard canReviewArchive, let pending = archivablePending, let requestStore else { return }
        do {
            let current = try scope()
            guard current == (pendingScope ?? stagedRequest?.scope) else { throw Failure.invalid }
            archiveReview = try requestStore.reviewArchive(scope: current, pending: pending)
            archiveReviewGeneration = generation
        } catch { requestRecoveryStatus = "unavailable" }
    }
    func cancelArchiveReview() { archiveReview = nil; archiveReviewGeneration = nil }
    func confirmArchive(scope: () throws -> String) {
        guard canReviewArchive, let review = archiveReview, archiveReviewGeneration == generation,
              let pending = archivablePending, let requestStore else { return }
        do {
            guard review.scope == (try scope()), review.scope == (pendingScope ?? stagedRequest?.scope) else { throw Failure.invalid }
            try requestStore.archive(review, pending: pending)
            let stillCurrent = (try? scope()) == review.scope
            reset()
            if stillCurrent { archiveUndo = review; requestRecoveryStatus = "archived" }
            // Release succeeded; identity change hides all old-scope UI instead of falsely reporting failure.
        } catch { archiveReview = nil; archiveReviewGeneration = nil; requestRecoveryStatus = "archive-failed" }
    }
    func undoArchive(scope: () throws -> String) {
        guard !busy, !writeInFlight, input.isEmpty, !hasDraft, !hasUnsubmittedRecovery,
              pendingWrite == nil, let review = archiveUndo, let requestStore else { return }
        do {
            guard review.scope == (try scope()) else { throw Failure.invalid }
            try requestStore.holdArchived(review)
            archiveUndo = nil; requestRecoveryStatus = "archive-held"
        } catch { requestRecoveryStatus = "archive-failed" }
    }
    func reconcileUncertain(request: Request, scope: () throws -> String) async {
        guard !busy, !writeInFlight, writeUncertain, let pending = pendingWrite else { return }
        serverReplyStatus = nil; serverReplyCode = nil
        let token = generation; busy = true; failed = false
        defer { if token == generation { busy = false } }
        do {
            let confirmed = try await readBack(pending, token: token, request: request, scope: scope)
            guard token == generation else { return }
            requestRecoveryStatus = confirmed ? "confirmed" : "unknown"
        } catch { if token == generation {
            if let reply = error as? ContextPrivateHTTPFailure { serverReplyStatus = reply.status; serverReplyCode = reply.code }
            failed = true
            if case Failure.durability = error { requestRecoveryStatus = "durability" }
            else if error is Failure || error is DecodingError || (error as NSError).domain == NSCocoaErrorDomain { requestRecoveryStatus = "invalid-receipt" }
            else { requestRecoveryStatus = "unknown" }
        } }
    }
    func retryUncertain(request: Request, scope: () throws -> String) async {
        // Unknown outcomes are reconciled through authenticated GET proofs only.
        // Missing proof never authorizes replaying an original POST/PUT/DELETE.
        await reconcileUncertain(request: request, scope: scope)
    }
    func stageSavedDraft(store: ContextPrivateDraftStore, scope: () throws -> String) {
        guard !busy, !writeInFlight, !hasDraft else { return }
        invalidateDraft(); stagedDraft = nil
        do {
            let owner = try scope()
            guard identity == nil || identity == owner else { throw Failure.invalid }
            let candidate = try store.load(scope: owner)
            guard try scope() == owner else { throw Failure.invalid }
            identity = owner; stagedDraft = candidate
            savedStatus = L("已载入本机草稿，正文隐藏；须联网核验来源。", "Local draft staged, text hidden; validate sources online.")
        } catch { savedStatus = L("草稿不可读取、密钥不可用或身份不匹配；原文件保留。", "Draft unreadable, key unavailable or identity mismatch; original file retained.") }
    }
    func saveDraft(store: ContextPrivateDraftStore, request: Request, scope: () throws -> String) async {
        guard !busy, !writeInFlight, hasDraft, draftValid else { return }
        let token = generation, epoch = draftEpoch
        do {
            let owner = try scope()
            guard owner == identity else { throw Failure.invalid }
            let snapshot = ContextPrivateSavedDraft(version: 1, scope: owner, text: draft,
                references: refs.map { .init(id: $0.0, revision: $0.1) }, savedAt: ISO8601DateFormatter().string(from: Date()))
            await reviewDraft(create: false, request: request, scope: scope)
            guard token == generation, epoch == draftEpoch, hasDraft, draftValid, try scope() == owner,
                  draft == snapshot.text, refs.count == snapshot.references.count,
                  zip(refs, snapshot.references).allSatisfy({ $0.0.0 == $0.1.id && $0.0.1 == $0.1.revision }) else { throw Failure.invalid }
            try store.save(snapshot)
            savedStatus = L("本次草稿已加密保存到本机；未上传或发布。", "This draft saved encrypted locally; not uploaded or published.")
        } catch ContextPrivateDraftStore.Failure.capacity {
            if token == generation { savedStatus = L("本机私人草稿容量已满；已有加密文件保留，未自动清理。", "Local private draft capacity reached; existing encrypted files retained, no automatic cleanup.") }
        } catch { if token == generation { savedStatus = L("保存未确认，未写入明文；可能已替换加密文件，请重新载入核对。", "Save unconfirmed; no plaintext write. The encrypted file may already have been replaced; reload to check.") } }
    }
    func revealSavedDraft(request: Request, scope: () throws -> String) async {
        guard !busy, !writeInFlight, !hasDraft, let candidate = stagedDraft else { return }
        let token = generation, epoch = draftEpoch; busy = true
        defer { if token == generation { busy = false } }
        do {
            guard try scope() == candidate.scope else { throw Failure.invalid }
            let payload = ["sources": candidate.references.map { ["id": $0.id, "revision": $0.revision] as [String: Any] }]
            let data = try await scoped("v1/context-private-reference-check", "POST", body(payload), request: request, scope: scope)
            guard token == generation, epoch == draftEpoch, identity == candidate.scope, try scope() == candidate.scope else { return }
            struct Gate: Decodable { let version: Int; let valid: Bool; let requiresUserReview: Bool; let meaning: String; let evidenceKind: String }
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard let object, Set(object.keys) == Set(["version", "valid", "requiresUserReview", "meaning", "evidenceKind"]) else { throw Failure.invalid }
            let gate = try JSONDecoder().decode(Gate.self, from: data)
            guard gate.version == 1, gate.valid, gate.requiresUserReview, gate.meaning == "current-authorized-private-revisions-only", gate.evidenceKind == "user-statement" else { throw Failure.invalid }
            refs = candidate.references.map { ($0.id, $0.revision) }; draft = candidate.text; hasDraft = true; draftValid = true
            stagedDraft = nil; savedStatus = L("已联网核验并恢复，仍是本人陈述草稿；未发布。", "Validated online and restored as a personal statement draft; not published.")
        } catch {
            guard token == generation, epoch == draftEpoch else { return }
            invalidateDraft(); clearList()
            savedStatus = L("来源已变更、不可访问或离线，正文继续隐藏；加密文件保留。", "Sources changed, inaccessible or offline; text remains hidden, encrypted file retained.")
        }
    }
    func recoverUnsubmitted(request: Request, scope: () throws -> String) async {
        guard canRecoverUnsubmitted, let recovery = unsubmittedRecovery else { return }
        let token = generation, valueEpoch = inputValueEpoch
        guard let expectedScope = recovery.scope, (try? scope()) == expectedScope else { failed = true; return }
        busy = true
        defer { if token == generation { busy = false } }
        do {
            var source: ContextPrivateSource?
            if let reference = recovery.reference {
                let data = try await scoped("v1/context-private/" + reference.id, "GET", nil, request: request, scope: scope)
                let value = try ContextPrivateSource.decode(data)
                guard !value.withdrawn, value.id == reference.id, value.eventId == reference.eventId, value.revision == reference.revision,
                      value.createdAt == reference.createdAt, value.revisionChangedAt == reference.changedAt,
                      value.projectId == recovery.project?.id, value.repositoryId == recovery.project?.repositoryID else { throw Failure.invalid }
                source = value
            } else {
                try await verifyNewProject(recovery.project, request: request, scope: scope)
            }
            guard token == generation, valueEpoch == inputValueEpoch, try scope() == expectedScope,
                  unsubmittedRecovery == recovery, input.isEmpty, inputTag.isEmpty, editing == nil,
                  !hasDraft, !writeInFlight, !writeUncertain, pendingWrite == nil, stagedRequest == nil,
                  try requestStore?.hasUnconfirmed(scope: expectedScope) != true else { throw Failure.invalid }
            editing = source; withdrawalReference = recovery.reference
            stagedInput = nil // Supersede hidden in-memory staging only; encrypted files remain untouched.
            resetInputProject(); selectedProject = recovery.project
            inputAttribution.begin(project: recovery.project, accountScope: expectedScope)
            inputLineage = recovery.editorId
            inputEditorEpoch += 1; input = recovery.text; inputTag = recovery.tag; inputLimit = false
            unsubmittedRecovery = nil; failed = false
        } catch { if token == generation { failed = true } }
    }
    func reviewDraft(create: Bool, request: Request, scope: () throws -> String) async {
        guard !busy, create ? (!sources.isEmpty && sources.count <= 100 && !hasDraft && !writeUncertain) : hasDraft else { return }
        var preparedDraft: String?
        if create {
            let value = "私人来源临时核对草稿；仅个人陈述，不证明工作完成。\n当前加载来源：\(sources.count)；还有分页：\(hasMore ? "是" : "否")；部分排除：\(omitted ? "是" : "否")。\n"
                + sources.map { "\n[\($0.id) · revision \($0.revision)]\n\($0.text ?? "")\n待补充成果、验证和未完成事项。\n" }.joined()
            guard value.unicodeScalars.count <= 300000 else { draftTooLarge = true; return }
            draftTooLarge = false; preparedDraft = value
        }
        let token = generation; let draftToken = draftEpoch; busy = true
        defer { if token == generation { busy = false } }
        do {
            let references = create ? sources.map { ($0.id, $0.revision) } : refs
            let payload = ["sources": references.map { ["id": $0.0, "revision": $0.1] as [String: Any] }]
            let data = try await scoped("v1/context-private-reference-check", "POST", body(payload), request: request, scope: scope)
            guard token == generation, draftToken == draftEpoch else { return }
            struct Gate: Decodable { let version: Int; let valid: Bool; let requiresUserReview: Bool; let meaning: String; let evidenceKind: String }
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard let object, Set(object.keys) == Set(["version", "valid", "requiresUserReview", "meaning", "evidenceKind"]) else { throw Failure.invalid }
            let gate = try JSONDecoder().decode(Gate.self, from: data)
            guard gate.version == 1, gate.valid, gate.requiresUserReview, gate.meaning == "current-authorized-private-revisions-only", gate.evidenceKind == "user-statement" else { throw Failure.invalid }
            refs = references; draftValid = true
            if let preparedDraft { hasDraft = true; draft = preparedDraft }

        } catch {
            guard token == generation, draftToken == draftEpoch else { return }
            if !input.isEmpty || !inputTag.isEmpty {
                let reference = editing.map { WithdrawalReference(id: $0.id, eventId: $0.eventId, revision: $0.revision, createdAt: $0.createdAt, changedAt: $0.revisionChangedAt) }
                unsubmittedRecovery = UnsubmittedRecovery(text: input, tag: inputTag, scope: identity, reference: reference, editorId: inputLineage, project: editing?.projectId.flatMap { id in editing?.repositoryId.map { InputBackupProjectSnapshot(id: id, repositoryID: $0) } } ?? inputAttribution.project)
                inputLineage = UUID().uuidString.lowercased() // New visible edits must not reuse the hidden editor lineage.
            }
            invalidateDraft(); clearList(); inputEditorEpoch += 1; editing = nil; input = ""; inputTag = ""; failed = true
        }
    }
}
