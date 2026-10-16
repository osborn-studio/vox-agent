import Foundation
import Observation
import CryptoKit
import Darwin

struct FeishuSendReceipt: Codable, Sendable {
    let eventId: String
    let conversationId: String
    let status: String
    let reason: String
    let messageId: String?
    var final: Bool? = nil

    func validates(_ record: FeishuPendingText) -> Bool {
        eventId == record.eventId && conversationId == record.conversationId &&
        ["pending", "sending", "sent", "unknown", "failed", "membership_lost"].contains(status) &&
        reason.range(of: "^[A-Z][A-Z0-9_]{0,63}$", options: .regularExpression) != nil &&
        (status == "sent" ? messageId?.range(of: "^om_[A-Za-z0-9_-]{1,253}$", options: .regularExpression) != nil : messageId == nil) &&
        (!(final == true && ["pending", "sending"].contains(status))) &&
        (!(final == false && ["sent", "failed", "membership_lost"].contains(status)))
    }
}

struct FeishuPendingText: Codable, Identifiable, Sendable {
    var id: String { eventId }
    let eventId: String
    let conversationId: String
    let text: String
    var status: String
    var accepted: Bool
    var messageId: String?
    var suspended: Bool? = nil
    var attempted: Bool? = nil
    var final: Bool? = nil
    var attempts: Int? = nil
    var retryAfter: Date? = nil

    var confirmedTerminal: Bool { accepted && ["sent", "failed", "membership_lost"].contains(status) }
    var compactable: Bool { accepted && (confirmedTerminal || final == true) }
    var stopped: Bool { suspended == true || final == true || confirmedTerminal }
}

enum FeishuSendingFailure: Error { case signedOut, expired, permission, unavailable, unsupported, invalid, conflict, rejected, storage, queueFull }

@MainActor protocol FeishuSendingTransport: AnyObject {
    var conversationIdentity: FeishuConversationIdentity? { get }
    func submitText(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt
    func textReceipt(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt
}

@MainActor struct FeishuOutboxPersistence {
    var load: (FeishuConversationIdentity) throws -> [FeishuPendingText]
    var save: (FeishuConversationIdentity, [FeishuPendingText]) throws -> Void

    static let maxRecords = 200
    static let captureBytes = 6_000_000 // 2 MiB reserved for receipts/status growth.
    static func validate(_ rows: [FeishuPendingText]) throws {
        guard rows.count <= maxRecords, Set(rows.map(\.eventId)).count == rows.count,
              try JSONEncoder().encode(rows).count + rows.filter({ !$0.confirmedTerminal }).count * 1024 <= 8_388_608 else { throw FeishuSendingFailure.storage }
        for r in rows {
            guard r.eventId.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil,
                  r.conversationId.range(of: "^fc_[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil,
                  !r.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  r.text.utf8.count <= 20_000, !r.text.contains("\0"),
                  ["pending", "sending", "sent", "unknown", "failed", "membership_lost"].contains(r.status),
                  r.status == "sent" ? (r.accepted && r.messageId?.range(of: "^om_[A-Za-z0-9_-]{1,253}$", options: .regularExpression) != nil) : r.messageId == nil,
                  !["sending", "failed", "membership_lost"].contains(r.status) || r.accepted,
                  !(r.accepted && r.attempted == false),
                  r.attempts == nil || (0...6).contains(r.attempts!),
                  !(r.attempted == false && (r.attempts ?? 0) > 0),
                  r.retryAfter == nil || r.retryAfter!.timeIntervalSince1970.isFinite,
                  !(r.final == true && (!r.accepted || ["pending", "sending"].contains(r.status))),
                  !(r.final == false && r.confirmedTerminal),
                  !(r.status == "unknown" && r.attempted == false) else { throw FeishuSendingFailure.storage }
        }
    }

    static func directory(_ root: URL) -> Self {
        func prepare() throws {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let attrs = try FileManager.default.attributesOfItem(atPath: root.path)
            guard attrs[.type] as? FileAttributeType == .typeDirectory,
                  (attrs[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
                  let permissions = attrs[.posixPermissions] as? NSNumber,
                  permissions.intValue & 0o077 == 0 else { throw FeishuSendingFailure.storage }
        }
        func file(_ identity: FeishuConversationIdentity) throws -> URL {
            let data = try JSONEncoder().encode([identity.origin, identity.appId, identity.tenantKey, identity.userId])
            let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            return root.appendingPathComponent(hash + ".json")
        }
        return Self(load: { identity in
            try prepare()
            let url = try file(identity)
            guard FileManager.default.fileExists(atPath: url.path) else { return [] }
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            guard attrs[.type] as? FileAttributeType == .typeRegular,
                  (attrs[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
                  let permissions = attrs[.posixPermissions] as? NSNumber,
                  permissions.intValue & 0o077 == 0,
                  (attrs[.size] as? NSNumber)?.intValue ?? Int.max <= 8_388_608 else { throw FeishuSendingFailure.storage }
            let records = try JSONDecoder().decode([FeishuPendingText].self, from: Data(contentsOf: url))
            try validate(records)
            return records
        }, save: { identity, records in
            try prepare()
            let url = try file(identity)
            try validate(records)
            let data = try JSONEncoder().encode(records)
            guard data.count <= 8_388_608 else { throw FeishuSendingFailure.storage }
            let temporary = root.appendingPathComponent(UUID().uuidString + ".tmp")
            let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
            guard fd >= 0 else { throw FeishuSendingFailure.storage }
            defer { close(fd); unlink(temporary.path) }
            let written = data.withUnsafeBytes { bytes -> Bool in
                var offset = 0
                while offset < bytes.count {
                    let count = Darwin.write(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                    if count <= 0 { return false }
                    offset += count
                }
                return true
            }
            guard written, fsync(fd) == 0, rename(temporary.path, url.path) == 0 else { throw FeishuSendingFailure.storage }
            let directory = open(root.path, O_RDONLY | O_NOFOLLOW)
            guard directory >= 0 else { throw FeishuSendingFailure.storage }
            defer { close(directory) }
            guard fsync(directory) == 0 else { throw FeishuSendingFailure.storage }
        })
    }
}

struct FeishuRecoveredDraft: Identifiable {
    let id = UUID()
    let conversationId: String
    let name: String
    let text: String
}

@MainActor @Observable final class FeishuConversationOutbox {
    private(set) var identity: FeishuConversationIdentity?
    private(set) var records: [FeishuPendingText] = [] { didSet { onTranslationSourcesChanged?() } }
    private(set) var destination: FeishuConversation? { didSet { onTranslationSourcesChanged?() } }
    private(set) var recoveredDrafts: [FeishuRecoveredDraft] = []
    var draft = ""
    private(set) var voiceDraftID: UUID?
    private var voiceDestination: FeishuConversation?
    var onVoiceDraftCancelled: ((UUID) -> Void)?
    private(set) var failure: FeishuSendingFailure?
    private(set) var flushing = false
    private let persistence: FeishuOutboxPersistence
    private let now: () -> Date
    private var generation: UInt64 = 0
    private var active: UUID?
    private var storageReady = false
    private var uncertainCapture: FeishuPendingText?
    private var nextBatch = 0
    var onTranslationSourcesChanged: (() -> Void)?
    var onForbidden: ((String) -> Void)?

    init(persistence: FeishuOutboxPersistence, now: @escaping () -> Date = Date.init) {
        self.persistence = persistence; self.now = now
    }

    func bind(_ value: FeishuConversationIdentity?) {
        guard identity != value else { return }
        generation &+= 1
        cancelVoiceDraft()
        identity = value
        records = []; draft = ""; destination = nil; failure = nil; recoveredDrafts = []
        active = nil; flushing = false; storageReady = false; nextBatch = 0; uncertainCapture = nil
        guard value != nil else { return }
        reloadStorage()
    }

    func reloadStorage() {
        guard !flushing, let identity else { return }
        do {
            var loaded = try persistence.load(identity)
            try FeishuOutboxPersistence.validate(loaded)
            // Pre-marker files cannot prove that a pending POST never happened.
            // Reconcile only by the original event, never generate replacements.
            for i in loaded.indices where !loaded[i].accepted && loaded[i].attempted != false {
                loaded[i].status = "unknown"; loaded[i].attempted = true
            }
            if let capture = uncertainCapture, let found = loaded.first(where: { $0.eventId == capture.eventId }) {
                guard found.text == capture.text && found.conversationId == capture.conversationId else { throw FeishuSendingFailure.storage }
                if draft == capture.text { draft = "" }
            }
            for i in loaded.indices where loaded[i].attempts == 6 && !loaded[i].confirmedTerminal {
                loaded[i].suspended = true; loaded[i].status = "unknown"
            }
            records = loaded; uncertainCapture = nil; storageReady = true; failure = nil
        } catch { storageReady = false; failure = .storage }
    }

    func select(_ value: FeishuConversation?) {
        guard destination != value else { return }
        let changed = destination?.id != value?.id || destination?.capability != value?.capability
        destination = value
        if changed { draft = "" }
    }

    func revoke(_ id: String) {
        if let selected = destination, selected.id == id, !draft.isEmpty {
            recoveredDrafts.append(FeishuRecoveredDraft(conversationId: id, name: selected.name, text: draft))
            draft = ""
        }
        // Recovery is captured before the AppState navigation callback selects an agent.
        onForbidden?(id)
        if destination?.id == id { select(nil) }
    }

    func discardRecovery(_ id: UUID) { recoveredDrafts.removeAll { $0.id == id } }

    /// Capture before recording. A renewed token keeps this stable account binding;
    /// bind(nil), even followed by the same account, invalidates the capture.
    func beginVoiceDraft() -> UUID? {
        guard voiceDraftID == nil, identity != nil,
              let destination, destination.capability == "text_send" else { return nil }
        let id = UUID()
        voiceDraftID = id; voiceDestination = destination
        return id
    }

    func cancelVoiceDraft() {
        let id = voiceDraftID
        voiceDraftID = nil; voiceDestination = nil
        if let id { onVoiceDraftCancelled?(id) }
    }

    @discardableResult func finishVoiceDraft(_ id: UUID, text: String) -> Bool {
        guard voiceDraftID == id, identity != nil, let frozen = voiceDestination else { return false }
        voiceDraftID = nil; voiceDestination = nil
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return true }
        // Memory only. Never enqueue, persist a HistoryRecord, or change selection.
        if destination?.id == frozen.id && destination?.capability == "text_send" {
            draft += draft.isEmpty ? text : "\n" + text
        } else {
            recoveredDrafts.append(FeishuRecoveredDraft(conversationId: frozen.id, name: frozen.name, text: text))
        }
        return true
    }

    private func compacted(_ rows: [FeishuPendingText]) -> [FeishuPendingText] {
        // This is a transport cache, not history. Only authoritative terminal
        // receipts backed by retained service originals can be compacted.
        let keep = Set(rows.filter(\.compactable).suffix(50).map(\.eventId))
        var result = rows.filter { !$0.compactable || keep.contains($0.eventId) }
        // Leave room for the next capture even when terminal text is heavily escaped.
        let captureSize = draft.utf8.count * 6 + 4096
        while result.count >= FeishuOutboxPersistence.maxRecords ||
              ((try? JSONEncoder().encode(result).count) ?? Int.max) + captureSize > FeishuOutboxPersistence.captureBytes {
            guard let index = result.firstIndex(where: { $0.compactable }) else { break }
            result.remove(at: index)
        }
        return result
    }

    var attentionRecords: [FeishuPendingText] {
        records.filter { ($0.status == "unknown" && $0.stopped) || $0.status == "membership_lost" }
    }
    var visibleRecords: [FeishuPendingText] { records.filter { $0.conversationId == destination?.id } }
    var capacityFull: Bool {
        let rows = compacted(records)
        return rows.count >= FeishuOutboxPersistence.maxRecords ||
            ((try? JSONEncoder().encode(rows).count) ?? Int.max) + draft.utf8.count * 6 + 4096 > FeishuOutboxPersistence.captureBytes
    }
    var storageBlocked: Bool { identity != nil && !storageReady }
    var hasRetryableOriginals: Bool {
        storageReady && records.contains { !$0.confirmedTerminal && $0.final != true }
    }
    var canSend: Bool {
        identity != nil && storageReady && !capacityFull && destination?.capability == "text_send" &&
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && draft.utf8.count <= 20_000 && !draft.contains("\0")
    }

    @discardableResult func enqueue() -> Bool {
        guard canSend, let identity, let destination else {
            if capacityFull { failure = .queueFull }
            return false
        }
        var record = FeishuPendingText(eventId: UUID().uuidString, conversationId: destination.id,
                                      text: draft, status: "pending", accepted: false, messageId: nil)
        record.attempted = false
        let next = compacted(records) + [record]
        do { try persistence.save(identity, next) }
        catch { uncertainCapture = record; storageReady = false; failure = .storage; return false }
        records = next; draft = ""; failure = nil
        return true
    }

    func flush(using transport: any FeishuSendingTransport, retryNow: Bool = false) async {
        guard !flushing, storageReady, let capturedIdentity = identity,
              capturedIdentity == transport.conversationIdentity else { return }
        let epoch = generation, operation = UUID()
        active = operation; flushing = true
        defer { if active == operation { active = nil; flushing = false } }
        let eligible = records.filter {
            !$0.confirmedTerminal && $0.final != true &&
            (retryNow || ($0.suspended != true && ($0.retryAfter ?? .distantPast) <= now())) &&
            ["pending", "unknown", "sending"].contains($0.status)
        }
        guard !eligible.isEmpty else { return }
        let start = nextBatch % eligible.count
        let batch = (Array(eligible[start...]) + Array(eligible[..<start])).prefix(20)
        nextBatch = (start + batch.count) % eligible.count
        for captured in batch {
            guard !Task.isCancelled, epoch == generation, capturedIdentity == identity,
                  capturedIdentity == transport.conversationIdentity else { return }
            var updated = captured
            updated.attempted = true; updated.status = "unknown"
            updated.attempts = min(6, (captured.attempts ?? 0) + 1)
            updated.retryAfter = now().addingTimeInterval(min(300, 5 * pow(2, Double(updated.attempts!))))
            updated.suspended = updated.attempts == 6
            // Commit uncertainty BEFORE any possible network effect, including GET
            // for an accepted submission. Cancellation cannot skip this marker.
            guard let markerIndex = records.firstIndex(where: { $0.eventId == captured.eventId }) else { return }
            var marked = records; marked[markerIndex] = updated
            do { try persistence.save(capturedIdentity, marked); records = marked }
            catch { storageReady = false; failure = .storage; return }
            var forbidden = false
            do {
                let receipt = try await (captured.accepted
                    ? transport.textReceipt(captured, for: capturedIdentity)
                    : transport.submitText(captured, for: capturedIdentity))
                guard receipt.validates(captured) else { throw FeishuSendingFailure.invalid }
                updated.status = receipt.status; updated.accepted = true; updated.messageId = receipt.messageId
                updated.final = receipt.final ?? ["sent", "failed", "membership_lost"].contains(receipt.status)
                updated.suspended = updated.final
                // The counter bounds consecutive failures, not successful status polls.
                updated.attempts = 0
                updated.retryAfter = now().addingTimeInterval(60)
                forbidden = receipt.status == "membership_lost"
            } catch {
                guard epoch == generation, capturedIdentity == identity else { return }
                let reason = (error as? FeishuSendingFailure) ?? .unavailable
                if case .expired = reason { bind(nil); failure = .expired; return }
                if case .signedOut = reason { bind(nil); failure = .signedOut; return }
                updated.status = "unknown" // Bare errors never prove non-delivery.
                switch reason {
                case .permission: updated.suspended = true; forbidden = true
                case .conflict, .rejected, .unsupported, .invalid: updated.suspended = true
                default: break
                }
                failure = reason
            }
            // Save uncertainty even if cancelled; account/generation still owns it.
            guard epoch == generation, capturedIdentity == identity,
                  capturedIdentity == transport.conversationIdentity,
                  let index = records.firstIndex(where: { $0.eventId == captured.eventId }) else { return }
            if updated.attempts == 6 && !updated.confirmedTerminal {
                updated.suspended = true; updated.status = "unknown"
            }
            var next = records; next[index] = updated
            do {
                try persistence.save(capturedIdentity, next); records = next
                if updated.status != "unknown" { failure = nil }
            } catch { storageReady = false; failure = .storage }
            if forbidden { revoke(captured.conversationId) }
            if !storageReady { return }
        }
    }
}
