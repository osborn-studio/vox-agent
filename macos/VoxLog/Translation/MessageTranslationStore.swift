import Foundation
import Observation

/// One limiter shared by all window stores. Admission is immediate, with no queue.
@MainActor final class TranslationAdmission {
    static let shared = TranslationAdmission()
    private var tickets: Set<UUID> = []
    var count: Int { tickets.count }
    func acquire() -> UUID? {
        guard tickets.count < 2 else { return nil }
        let ticket = UUID(); tickets.insert(ticket); return ticket
    }
    func release(_ ticket: UUID) { tickets.remove(ticket) }
}

@MainActor struct MessageTranslationService {
    var snapshot: () throws -> TranslationConfiguration
    var completion: (String, TranslationConfiguration) async throws -> TranslationCompletion
    static var live: Self {
        Self(snapshot: { try SelectedLLMConfigurationResolver.snapshot() },
             completion: { try await TranslationTransport.complete(source: $0, config: $1) })
    }
    func validate(_ text: String) throws -> TranslationProtection {
        guard text.utf8.count <= 6_000 else { throw TranslationFailure.inputLimit }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw TranslationFailure.empty }
        return try TranslationProtection(text)
    }
}

@MainActor @Observable final class MessageTranslationStore {
    enum State: Equatable {
        case running, result(String, expanded: Bool, identical: Bool), failure(TranslationFailure), onlyCode
    }
    private struct Identity: Equatable {
        let key: String
        let scope: String
        let version: Data
        let allowed: Bool
        init(_ source: TranslationSource) { key = source.key; scope = source.scope; version = source.version; allowed = source.allowed }
    }
    private struct Entry {
        let source: Identity
        let current: () -> TranslationSource?
        let fingerprint: String
        var state: State
        var request: UUID?
        var task: Task<Void, Never>?
        var bytes: Int {
            128 + source.key.utf8.count + source.scope.utf8.count + fingerprint.utf8.count + { if case .result(let text, _, _) = state { return text.utf8.count }; return 0 }()
        }
    }
    private var entries: [String: Entry] = [:]
    private var order: [String] = []
    private let service: MessageTranslationService
    private let admission: TranslationAdmission
    private let settings: TranslationSettings
    private var observer: UUID?
    private(set) var visible = false
    var count: Int { entries.count }
    var bytes: Int { entries.values.reduce(0) { $0 + $1.bytes } }
    var enabled: Bool { settings.enabled }

    init(service: MessageTranslationService? = nil, admission: TranslationAdmission? = nil,
         settings: TranslationSettings? = nil) {
        self.service = service ?? .live; self.admission = admission ?? .shared
        self.settings = settings ?? .shared
        observer = self.settings.observe { [weak self] in self?.clear() }
    }
    func state(_ key: String) -> State? { entries[key]?.state }
    func setVisible(_ value: Bool) {
        guard visible != value else { return }
        visible = value; if !value { clear() }
    }
    func clear() {
        for entry in entries.values { entry.task?.cancel() }
        entries = [:]; order = []
    }
    func remove(_ key: String) {
        entries.removeValue(forKey: key)?.task?.cancel(); order.removeAll { $0 == key }
    }
    /// Called synchronously by source owners, never delayed to SwiftUI onChange.
    func reconcile() {
        for (key, entry) in entries {
            if entry.current().map(Identity.init) != entry.source || !entry.source.allowed { remove(key) }
        }
    }
    func cancel(_ key: String) { remove(key) }
    func collapse(_ key: String) {
        guard var entry = entries[key], case .result(let text, _, let identical) = entry.state else { return }
        guard entry.current().map(Identity.init) == entry.source, entry.source.allowed else { remove(key); return }
        entry.state = .result(text, expanded: false, identical: identical); entries[key] = entry
    }
    func copyOriginal(current: () -> TranslationSource?, copy: (String) -> Void) {
        guard visible, let source = current() else { return }; copy(source.text)
    }
    func copyResult(_ key: String, copy: (String) -> Void) {
        reconcile()
        guard enabled, visible, let entry = entries[key], case .result(let text, _, _) = entry.state else { return }
        copy(text)
    }
    func translate(current: @escaping () -> TranslationSource?, retry: Bool = false) {
        guard enabled, visible, let source = current() else { return }
        reconcile()
        let key = source.key
        if entries[key]?.state == .running { return }
        if let entry = entries[key], !retry {
            if case .result(let text, let expanded, let identical) = entry.state {
                entries[key]?.state = .result(text, expanded: !expanded, identical: identical); return
            }
        }
        remove(key)
        // Bound all states, including failures; no unbounded error dictionary.
        while entries.count >= 100 || bytes + 128 + source.key.utf8.count + source.scope.utf8.count + 4608 > 4 * 1024 * 1024 {
            guard let victim = order.first(where: { entries[$0]?.state != .running }) else { return }; remove(victim)
        }
        // Oversized originals are owned by their source, not duplicated in the cache.
        entries[key] = Entry(source: Identity(source), current: current, fingerprint: "", state: .running)
        order.append(key)
        do {
            guard source.allowed else { throw TranslationFailure.denied }
            let protection = try service.validate(source.text)
            if protection.onlyCode { entries[key]?.state = .onlyCode; return }
            let config = try service.snapshot()
            _ = try TranslationProtocol.request(source.text, config: config)
            guard let slot = admission.acquire() else { throw TranslationFailure.busy }
            let request = UUID(), generation = settings.generation
            entries[key] = Entry(source: Identity(source), current: current, fingerprint: config.fingerprint, state: .running, request: request)
            let completion = service.completion, admission = admission
            entries[key]?.task = Task { [weak self] in
                defer { admission.release(slot) }
                do {
                    let result = try await completion(source.text, config)
                    try Task.checkCancellation()
                    guard result.complete, result.finishReason == (config.provider == .claude ? "end_turn" : "stop") else { throw TranslationFailure.incomplete }
                    guard result.text.utf8.count <= 65_536 else { throw TranslationFailure.outputLimit }
                    guard !result.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw TranslationFailure.empty }
                    try protection.validate(result.text)
                    guard let self, self.accepts(key, request, generation) else { return }
                    while self.bytes + result.text.utf8.count > 4 * 1024 * 1024 {
                        guard let victim = self.order.first(where: { $0 != key && self.entries[$0]?.state != .running }) else { break }; self.remove(victim)
                    }
                    self.entries[key]?.state = .result(result.text, expanded: true, identical: Data(result.text.utf8) == Data(source.text.utf8))
                    self.entries[key]?.task = nil; self.entries[key]?.request = nil
                } catch {
                    guard let self, self.accepts(key, request, generation) else { return }
                    self.entries[key]?.state = .failure((error as? TranslationFailure) ?? .network)
                    self.entries[key]?.task = nil; self.entries[key]?.request = nil
                }
            }
        } catch { entries[key]?.state = .failure((error as? TranslationFailure) ?? .network) }
    }
    private func accepts(_ key: String, _ request: UUID, _ generation: UInt64) -> Bool {
        guard enabled, visible, settings.generation == generation, let entry = entries[key], entry.request == request,
              entry.source.allowed, entry.current().map(Identity.init) == entry.source else { removeIfOwned(key, request); return false }
        return true
    }
    private func removeIfOwned(_ key: String, _ request: UUID) {
        if entries[key]?.request == request { remove(key) }
    }
}
