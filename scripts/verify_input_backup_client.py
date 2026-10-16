#!/usr/bin/env python3
"""Exercise the real Swift queue without XCTest; no network/keychain access.

For CLT-only Macs. This supplements, never replaces, the normal Swift test suite.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

STUB = '''import Foundation
func L(_ chinese: String, _ english: String) -> String { english }
enum KeychainService { static func load(key: String) -> String? { nil }; static func loadScalarWithoutInteraction(key: String) -> String? { nil } }
'''
HARNESS = r'''
import Foundation

@main struct Check {
    @MainActor static func main() async throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let team = InputBackupConfiguration.teamEndpoint
        let target = InputBackupConfiguration.teamTargetID
        precondition(team == "https://api.osbornvox.com")
        precondition(target == "oc_0c9ba5130567117bf48f995cfda00964")
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: team, availableTargets: []) == target)
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: "https://API.osbornvox.com:443/", availableTargets: ["vox", target]) == target)
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: team, availableTargets: ["vox"]) == "vox")
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: team, availableTargets: ["a", "b"]) == "")
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: "https://other.test", availableTargets: []) == "")
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: "http://127.0.0.1:8766", availableTargets: []) == "")
        precondition(InputBackupConfiguration.suggestedTarget(endpoint: "https://other.test", availableTargets: ["custom"]) == "custom")

        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        let equivalent = try InputBackupConfiguration(endpoint: "https://BACKUP.example.test:443/", targetID: "archive")
        precondition(equivalent == config)
        let queue = InputBackupClient(directory: root, credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) })
        try queue.enqueue(eventID: "event-1", text: "我想打通 Vox Agent 和飞书。", configuration: config)
        try queue.enqueue(eventID: "event-1", text: "我想打通 Vox Agent 和飞书。", configuration: config)
        precondition(queue.pendingCount == 1)
        do {
            try queue.enqueue(eventID: "event-1", text: "changed", configuration: config)
            fatalError("conflicting event accepted")
        } catch InputBackupError.conflict {}
        let bytes = try Data(contentsOf: root.appendingPathComponent("pending.json"))
        precondition(!String(decoding: bytes, as: UTF8.self).contains("original-token"))
        let permissions = try FileManager.default.attributesOfItem(atPath: root.appendingPathComponent("pending.json").path)
        precondition((permissions[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        let other = InputBackupClient(directory: root, credential: { try! InputBackupClient.bindCredential("another-account-token", to: config.endpoint) }, send: { _ in
            fatalError("sent previous account data")
        })
        await other.flush(configuration: config)
        precondition(other.pendingCount == 1)
        func copyQueue(_ name: String) throws -> URL {
            let target = root.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            for file in ["pending.json", "scope-salt"] {
                try FileManager.default.copyItem(at: root.appendingPathComponent(file), to: target.appendingPathComponent(file))
            }
            return target
        }
        var ambiguousPosts = 0
        let wrongReply = InputBackupClient(directory: try copyQueue("wrong"), credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }, send: { _ in
            ambiguousPosts += 1
            return (Data(#"{"eventId":"wrong","status":"pending"}"#.utf8), 202)
        })
        await wrongReply.flush(configuration: config)
        precondition(wrongReply.pendingCount == 1)
        try wrongReply.configure(config)
        precondition(wrongReply.deliveryState(eventID: "event-1") == .unknown)
        await wrongReply.flush(configuration: config)
        precondition(wrongReply.pendingCount == 1 && ambiguousPosts == 1, "ambiguous acceptance must retain original without repost")
        let offline = InputBackupClient(directory: try copyQueue("offline"), credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }, send: { _ in
            throw URLError(.timedOut)
        })
        await offline.flush(configuration: config)
        precondition(offline.pendingCount == 1)
        let blocked = InputBackupClient(directory: try copyQueue("blocked"), credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }, send: { _ in
            (Data(#"{"eventId":"event-1","status":"blocked"}"#.utf8), 202)
        })
        await blocked.flush(configuration: config)
        precondition(blocked.pendingCount == 1 && blocked.statusText.contains("blocked"))
        let accepted = InputBackupClient(directory: root, credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }, send: { request in
            precondition(request.value(forHTTPHeaderField: "Authorization") == "Bearer original-token")
            if request.httpMethod == "GET" {
                return (Data(#"{"eventId":"event-1","status":"pending"}"#.utf8), 200)
            }
            let payload = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            precondition(payload["eventId"] == "event-1")
            precondition(payload["text"] == "我想打通 Vox Agent 和飞书。")
            return (Data(#"{"eventId":"event-1","status":"pending"}"#.utf8), 202)
        })
        try accepted.configure(config)
        await accepted.refreshReceipts(configuration: config, eventID: "event-1")
        await accepted.flush(configuration: config)
        precondition(accepted.pendingCount == 0)
        precondition(InputBackupClient(directory: root, credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }).pendingCount == 0)
        for address in ["http://remote.example.test", "https://user:password@example.test", "https://example.test?token=secret", "https://example.test/other"] {
            do { _ = try InputBackupConfiguration(endpoint: address, targetID: "archive"); fatalError("unsafe URL") }
            catch InputBackupError.configuration {}
        }
        _ = try InputBackupConfiguration(endpoint: "http://127.0.0.1:8766", targetID: "archive")
        let corrupt = root.appendingPathComponent("corrupt")
        try FileManager.default.createDirectory(at: corrupt, withIntermediateDirectories: true)
        try Data("broken".utf8).write(to: corrupt.appendingPathComponent("pending.json"))
        let damaged = InputBackupClient(directory: corrupt, credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) })
        do { try damaged.enqueue(eventID: "event-2", text: "text", configuration: config); fatalError("overwrote corruption") }
        catch InputBackupError.storage {}
        precondition(try Data(contentsOf: corrupt.appendingPathComponent("pending.json")) == Data("broken".utf8))
        var pausing: InputBackupClient!
        var calls = 0
        pausing = InputBackupClient(directory: root.appendingPathComponent("pause"), credential: { try! InputBackupClient.bindCredential("original-token", to: config.endpoint) }, send: { request in
            calls += 1
            try pausing.configure(nil)
            let data = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            return (try JSONSerialization.data(withJSONObject: ["eventId": data["eventId"]!, "status": "pending"]), 202)
        })
        try pausing.enqueue(eventID: "one", text: "first", configuration: config)
        try pausing.enqueue(eventID: "two", text: "second", configuration: config)
        await pausing.flush(configuration: config)
        precondition(calls == 1 && pausing.pendingCount == 1)
        let bound = try InputBackupClient.bindCredential("vox_sso_secret", to: config.endpoint)
        precondition(InputBackupClient.credentialToken(bound, for: config.endpoint) == "vox_sso_secret")
        precondition(InputBackupClient.credentialToken(bound, for: URL(string: "https://attacker.test")!) == nil)
        precondition(InputBackupClient.credentialToken("vox_sso_unbound", for: config.endpoint) == nil)
        var switchingActor = try InputBackupClient.bindCredential("actor-before", to: config.endpoint)
        let initialActor = switchingActor
        var switchedPosts = 0
        let switched = InputBackupClient(directory: root.appendingPathComponent("post-actor-switch"), credential: { switchingActor }, send: { _ in
            switchedPosts += 1
            switchingActor = try InputBackupClient.bindCredential("actor-after", to: config.endpoint)
            return (Data(#"{"eventId":"actor-switch","status":"pending"}"#.utf8), 202)
        })
        try switched.configure(config)
        try switched.enqueue(eventID: "actor-switch", text: "synthetic", configuration: config)
        await switched.flush(configuration: config)
        precondition(switched.deliveryState(eventID: "actor-switch") == nil && switched.retainedInputs().isEmpty)
        switchingActor = initialActor
        precondition(switched.deliveryState(eventID: "actor-switch") == .pending)
        await switched.flush(configuration: config)
        precondition(switchedPosts == 1 && switched.pendingCount == 0)
        do { try switched.enqueue(eventID: "newline\n", text: "fixture", configuration: config); fatalError("newline event accepted") } catch InputBackupError.limit {}
        let empty = InputBackupClient(directory: root.appendingPathComponent("empty"), credential: { fatalError("empty queue accessed Keychain") })
        await empty.flush(configuration: config)
        let renewRoot = root.appendingPathComponent("renewal")
        var activeRaw = try InputBackupClient.bindCredential("vox_sso_old", to: config.endpoint)
        var delivered: [String] = []
        let renewableQueue = InputBackupClient(directory: renewRoot, credential: { activeRaw }, send: { req in
            let body = try JSONSerialization.jsonObject(with: req.httpBody!) as! [String: String]
            delivered.append(body["eventId"]!)
            precondition(req.value(forHTTPHeaderField: "Authorization") == "Bearer vox_sso_new")
            return (try JSONSerialization.data(withJSONObject: ["eventId": body["eventId"]!, "status":"pending"]), 202)
        })
        try renewableQueue.configure(config)
        try renewableQueue.enqueue(eventID: "old-pending", text: "retained", configuration: config)
        activeRaw = String(decoding: try JSONEncoder().encode(["token":"vox_sso_new", "origin":config.endpoint.absoluteString,
            "scopeIdentity":"verified-alice", "legacyToken":"vox_sso_old"]), as: UTF8.self)
        try renewableQueue.enqueue(eventID: "old-pending", text: "retained", configuration: config)
        precondition(renewableQueue.pendingCount == 1, "legacy pending must deduplicate after renewal")
        do { try renewableQueue.enqueue(eventID: "old-pending", text: "changed", configuration: config); fatalError("legacy pending conflict missed") } catch InputBackupError.conflict {}
        try renewableQueue.enqueue(eventID: "new-pending", text: "new", configuration: config)
        // Another identity has neither the stable scope nor a verified legacy alias.
        let alice = activeRaw
        activeRaw = String(decoding: try JSONEncoder().encode(["token":"bob", "origin":config.endpoint.absoluteString,
            "scopeIdentity":"verified-bob"]), as: UTF8.self)
        await renewableQueue.flush(configuration: config)
        precondition(delivered.isEmpty && renewableQueue.pendingCount == 2)
        activeRaw = alice
        await renewableQueue.flush(configuration: config)
        precondition(Set(delivered) == Set(["old-pending", "new-pending"]) && renewableQueue.pendingCount == 0)
        precondition(renewableQueue.configuration == config)
        let receiptDir = root.appendingPathComponent("receipts")
        var actor = try InputBackupClient.bindCredential("alice", to: config.endpoint)
        var posts = 0
        var gets = 0
        var queryStatus = "delivered"
        var queryEvent = "receipt-event"
        var queryMessage = "om_synthetic"
        var changeActorInFlight = false
        let tracking = InputBackupClient(directory: receiptDir, credential: { actor }, send: { request in
            if request.httpMethod == "POST" {
                posts += 1
                return (Data(#"{"eventId":"receipt-event","status":"pending"}"#.utf8), 202)
            }
            gets += 1
            precondition(request.httpMethod == "GET" && request.httpBody == nil)
            if changeActorInFlight { actor = try InputBackupClient.bindCredential("bob", to: config.endpoint) }
            return (try JSONSerialization.data(withJSONObject: ["eventId": queryEvent, "status": queryStatus, "messageId":queryMessage]), 200)
        })
        try tracking.configure(config)
        try tracking.enqueue(eventID: "receipt-event", text: "synthetic context", configuration: config)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .local)
        let beforeAcknowledgement = try Data(contentsOf: receiptDir.appendingPathComponent("pending.json"))
        await tracking.flush(configuration: config)
        // Simulate a crash after receipt persistence but before queue removal.
        try beforeAcknowledgement.write(to: receiptDir.appendingPathComponent("pending.json"))
        let afterCrash = InputBackupClient(directory: receiptDir, credential: { actor }, send: { _ in fatalError("POST after durable receipt") })
        await afterCrash.flush(configuration: config)
        precondition(afterCrash.pendingCount == 0)
        precondition(posts == 1 && tracking.pendingCount == 0)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .pending)
        queryEvent = "other-event"
        await tracking.refreshReceipts(configuration: config)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .pending)
        queryEvent = "receipt-event"
        queryMessage = "om_synthetic\n"
        await tracking.refreshReceipts(configuration: config)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .pending, "newline message ID accepted")
        queryMessage = "om_synthetic"
        changeActorInFlight = true
        await tracking.refreshReceipts(configuration: config)
        precondition(tracking.deliveryState(eventID: "receipt-event") == nil)
        actor = try InputBackupClient.bindCredential("alice", to: config.endpoint)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .pending)
        changeActorInFlight = false
        queryStatus = "unknown"
        await tracking.refreshReceipts(configuration: config)
        precondition(tracking.deliveryState(eventID: "receipt-event") == .unknown)
        await tracking.flush(configuration: config)
        try tracking.enqueue(eventID: "receipt-event", text: "synthetic context", configuration: config)
        precondition(posts == 1 && tracking.pendingCount == 0)
        do {
            try tracking.enqueue(eventID: "receipt-event", text: "different", configuration: config)
            fatalError("receipt payload conflict accepted")
        } catch InputBackupError.conflict {}
        await tracking.refreshReceipts(configuration: config)
        precondition(gets == 4, "unknown should require explicit status check")
        queryStatus = "delivered"
        await tracking.refreshReceipts(configuration: config, eventID: "receipt-event")
        precondition(tracking.deliveryState(eventID: "receipt-event") == .delivered)
        let restoredReceipts = InputBackupClient(directory: receiptDir, credential: { actor }, send: { _ in
            fatalError("delivered record queried or resent after restart")
        })
        try restoredReceipts.configure(config)
        precondition(restoredReceipts.deliveryState(eventID: "receipt-event") == .delivered)
        await restoredReceipts.refreshReceipts(configuration: config)
        await restoredReceipts.flush(configuration: config)
        let receiptBytes = try Data(contentsOf: receiptDir.appendingPathComponent("receipts-v1.json"))
        precondition(!String(decoding: receiptBytes, as: UTF8.self).contains("synthetic context"))
        precondition(!String(decoding: receiptBytes, as: UTF8.self).contains("alice"))
        let receiptMode = try FileManager.default.attributesOfItem(atPath: receiptDir.appendingPathComponent("receipts-v1.json").path)
        precondition((receiptMode[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        precondition(gets == 5)
        actor = String(decoding: try JSONEncoder().encode(["token":"vox_sso_new", "origin":config.endpoint.absoluteString,
            "scopeIdentity":"verified-alice", "legacyToken":"alice"]), as: UTF8.self)
        try restoredReceipts.enqueue(eventID: "receipt-event", text: "synthetic context", configuration: config)
        precondition(restoredReceipts.pendingCount == 0, "verified legacy receipt must prevent renewed-account resend")
        let capacityDir = root.appendingPathComponent("receipt-capacity")
        try FileManager.default.createDirectory(at: capacityDir, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: receiptDir.appendingPathComponent("scope-salt"), to: capacityDir.appendingPathComponent("scope-salt"))
        let sample = (try JSONSerialization.jsonObject(with: receiptBytes) as! [[String: Any]])[0]
        let full = (0..<5000).map { index -> [String: Any] in
            var entry = sample; entry["eventID"] = "capacity-\(index)"; return entry
        }
        try JSONSerialization.data(withJSONObject: full).write(to: capacityDir.appendingPathComponent("receipts-v1.json"))
        let capacity = InputBackupClient(directory: capacityDir, credential: { actor }, send: { _ in fatalError("POST when receipt capacity full") })
        do {
            try capacity.enqueue(eventID: "over-capacity", text: "retain draft", configuration: config)
            fatalError("capacity must reject before POST")
        } catch InputBackupError.limit {}
        var getCount = 0
        var observedEvents: Set<String> = []
        let bounded = InputBackupClient(directory: root.appendingPathComponent("bounded-receipts"), credential: { actor }, send: { request in
            let event: String
            if request.httpMethod == "GET" {
                getCount += 1
                event = request.url!.lastPathComponent
                observedEvents.insert(event)
            } else {
                event = (try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String])["eventId"]!
            }
            return (try JSONSerialization.data(withJSONObject: ["eventId":event, "status":"pending"]), request.httpMethod == "GET" ? 200 : 202)
        })
        try bounded.configure(config)
        for index in 0..<25 { try bounded.enqueue(eventID: "bounded-\(index)", text: "fixture", configuration: config) }
        await bounded.flush(configuration: config)
        await bounded.flush(configuration: config)
        precondition(bounded.pendingCount == 0)
        await bounded.refreshReceipts(configuration: config)
        precondition(getCount == 20)
        await bounded.refreshReceipts(configuration: config)
        precondition(getCount == 40 && observedEvents.count == 25)
        var resumedPosts = 0
        let renewalPause = InputBackupClient(directory: root.appendingPathComponent("renewal-pause"), credential: { actor }, send: { _ in
            resumedPosts += 1
            return (Data(), 500)
        })
        try renewalPause.configure(config)
        try renewalPause.enqueue(eventID: "renewal-pause", text: "retain paused draft", configuration: config)
        renewalPause.ensureCredential = {
            try! renewalPause.configure(nil)
            return true
        }
        await renewalPause.flush(configuration: config)
        precondition(resumedPosts == 0 && renewalPause.pendingCount == 1)
        try renewalPause.configure(config)
        renewalPause.ensureCredential = {
            try! renewalPause.configure(InputBackupConfiguration(endpoint: "https://other.example.test", targetID: "other"))
            return true
        }
        await renewalPause.flush(configuration: config)
        precondition(resumedPosts == 0 && renewalPause.pendingCount == 1)
        var starvationPosts: [String] = []
        let starvation = InputBackupClient(directory: root.appendingPathComponent("blocked-starvation"), credential: { actor }, send: { request in
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            let event = body["eventId"]!
            starvationPosts.append(event)
            return (try JSONSerialization.data(withJSONObject: ["eventId":event, "status":event == "unblocked-last" ? "pending" : "blocked"]), 202)
        })
        try starvation.configure(config)
        for index in 0..<20 { try starvation.enqueue(eventID: "blocked-\(index)", text: "retained failure", configuration: config) }
        try starvation.enqueue(eventID: "unblocked-last", text: "later valid input", configuration: config)
        for _ in 0..<21 { await starvation.flush(configuration: config) }
        precondition(starvationPosts.count == 21 && starvationPosts.last == "unblocked-last")
        precondition(starvation.pendingCount == 20 && starvation.deliveryState(eventID: "unblocked-last") == .pending)
        precondition(!InputBackupProjectSnapshot(id: "vox\n", repositoryID: "team/vox").isValid)
        precondition(!InputBackupProjectSnapshot(id: "vox", repositoryID: "team/vox\n").isValid)
        let snapshot = InputBackupProjectSnapshot(id: "vox", repositoryID: "team/vox")
        var draft = ContextProjectDraft()
        draft.begin(project: snapshot)
        draft.begin(project: InputBackupProjectSnapshot(id: "other", repositoryID: "team/other"))
        precondition(draft.project == snapshot && draft.frozen)
        draft.reset()
        draft.begin(project: nil)
        draft.begin(project: snapshot)
        precondition(draft.project == nil && draft.frozen, "unknown attribution is frozen too")
        var catalogActor = try InputBackupClient.bindCredential("catalog-alice", to: config.endpoint)
        var changeCatalogActor = false
        var catalogVersion = 1
        var catalogRepo = "team/vox"
        let catalog = InputBackupClient(directory: root.appendingPathComponent("catalog"), credential: { catalogActor }, send: { request in
            precondition(request.httpMethod == "GET" && request.url!.path == "/v1/context-projects" && request.httpBody == nil)
            if changeCatalogActor { catalogActor = try InputBackupClient.bindCredential("catalog-bob", to: config.endpoint) }
            return (try JSONSerialization.data(withJSONObject: ["version":catalogVersion, "projects":[["id":"vox", "name":"Vox", "repositoryId":catalogRepo]]]), 200)
        })
        try catalog.configure(config)
        let originalScope = try catalog.attributionScope()
        let available = try await catalog.fetchProjects()
        precondition(available.count == 1 && available[0].snapshot == snapshot)
        catalogRepo = "/Users/private/repo"
        do { _ = try await catalog.fetchProjects(); fatalError("catalog accepted local path") } catch InputBackupError.configuration {}
        catalogRepo = "team/vox"; catalogVersion = 2
        do { _ = try await catalog.fetchProjects(); fatalError("unknown catalog version accepted") } catch InputBackupError.configuration {}
        catalogActor = try InputBackupClient.bindCredential("catalog-bob", to: config.endpoint)
        do { try catalog.enqueue(eventID: "cross-actor-draft", text: "retained draft", configuration: config, expectedScope: originalScope); fatalError("draft crossed credential scope") } catch InputBackupError.credential {}
        catalogActor = try InputBackupClient.bindCredential("catalog-alice", to: config.endpoint)
        catalogVersion = 1; changeCatalogActor = true
        do { _ = try await catalog.fetchProjects(); fatalError("old actor catalog accepted") } catch InputBackupError.credential {}
        var attributedPosts = 0
        let attributedDir = root.appendingPathComponent("attributed")
        let attributed = InputBackupClient(directory: attributedDir, credential: { actor }, send: { request in
            attributedPosts += 1
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            precondition(body["projectId"] == "vox" && body["repoSource"] == "manual" && body["repository"] == "team/vox")
            return (Data(#"{"eventId":"attributed","status":"pending","attribution":{"version":1,"projectId":"vox","repositoryId":"team/vox"}}"#.utf8), 202)
        })
        try attributed.configure(config)
        try attributed.enqueue(eventID: "attributed", text: "frozen project", configuration: config, project: snapshot)
        let v2Data = try Data(contentsOf: attributedDir.appendingPathComponent("pending.json"))
        let envelope = try JSONSerialization.jsonObject(with: v2Data) as! [String: Any]
        precondition(envelope["version"] as? Int == 2)
        do { _ = try JSONDecoder().decode([[String:String]].self, from: v2Data); fatalError("old client could silently erase project") } catch is DecodingError {}
        let restoredAttribution = InputBackupClient(directory: attributedDir, credential: { actor }, send: { request in
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
            precondition(body["projectId"] == "vox" && body["repository"] == "team/vox")
            return (Data(#"{"eventId":"attributed","status":"pending","attribution":{"version":1,"projectId":"vox","repositoryId":"team/vox"}}"#.utf8), 202)
        })
        await restoredAttribution.flush(configuration: config)
        try restoredAttribution.enqueue(eventID: "attributed", text: "frozen project", configuration: config, project: snapshot)
        do {
            try restoredAttribution.enqueue(eventID: "attributed", text: "frozen project", configuration: config, project: InputBackupProjectSnapshot(id: "other", repositoryID: "team/other"))
            fatalError("event silently rebound to another project")
        } catch InputBackupError.conflict {}
        precondition(attributedPosts == 0 && restoredAttribution.pendingCount == 0)
        for echoCase in 0..<4 {
            var echoPosts = 0
            var confirmEcho = false
            let echoDir = root.appendingPathComponent("echo-\(echoCase)")
            let echoQueue = InputBackupClient(directory: echoDir, credential: { actor }, send: { request in
                if request.httpMethod == "POST" { echoPosts += 1 }
                var reply: [String:Any] = ["eventId":"echo-event", "status":request.httpMethod == "GET" ? "delivered" : "pending", "messageId":"om_echo"]
                if confirmEcho {
                    reply["attribution"] = ["version":1, "projectId":"vox", "repositoryId":"team/vox"]
                } else if echoCase == 1 {
                    reply["attribution"] = ["version":1, "projectId":"other", "repositoryId":"team/other"]
                } else if echoCase == 2 {
                    reply["attribution"] = ["version":2, "projectId":"vox", "repositoryId":"team/vox"]
                } else if echoCase == 3 {
                    reply["attribution"] = ["version":true, "projectId":"vox", "repositoryId":"team/vox"]
                }
                return (try JSONSerialization.data(withJSONObject: reply), request.httpMethod == "POST" ? 202 : 200)
            })
            try echoQueue.configure(config)
            try echoQueue.enqueue(eventID: "echo-event", text: "synthetic attribution", configuration: config, project: snapshot)
            await echoQueue.flush(configuration: config)
            await echoQueue.flush(configuration: config)
            precondition(echoQueue.deliveryState(eventID: "echo-event") == .unknown && echoPosts == 1 && echoQueue.pendingCount == 1)
            let review = echoQueue.retainedInputs()
            precondition(review.count == 1 && review[0].text == "synthetic attribution" && review[0].project == snapshot && review[0].state == .unknown)
            precondition(echoQueue.retainedInputs(limit: 0).isEmpty && echoQueue.canReview(review[0]))
            let previewActor = actor
            actor = try InputBackupClient.bindCredential("other-preview-actor", to: config.endpoint)
            precondition(echoQueue.retainedInputs().isEmpty && !echoQueue.canReview(review[0]), "stale rendered copy crossed actor")
            actor = previewActor
            if echoCase == 0 {
                let migrationDir = root.appendingPathComponent("old-project-receipt")
                try FileManager.default.copyItem(at: echoDir, to: migrationDir)
                let oldReceiptFile = migrationDir.appendingPathComponent("receipts-v1.json")
                var oldReceipts = try JSONSerialization.jsonObject(with: Data(contentsOf: oldReceiptFile)) as! [[String:Any]]
                oldReceipts[0].removeValue(forKey: "project")
                oldReceipts[0]["state"] = "pending"
                try JSONSerialization.data(withJSONObject: oldReceipts).write(to: oldReceiptFile)
                let migrated = InputBackupClient(directory: migrationDir, credential: { actor }, send: { request in
                    precondition(request.httpMethod == "GET", "migration cannot repost")
                    return (Data(#"{"eventId":"echo-event","status":"delivered","messageId":"om_echo","attribution":{"version":1,"projectId":"vox","repositoryId":"team/vox"}}"#.utf8), 200)
                })
                try migrated.configure(config)
                precondition(migrated.deliveryState(eventID: "echo-event") == .unknown && migrated.retainedInputs().count == 1)
                await migrated.refreshReceipts(configuration: config, eventID: "echo-event")
                await migrated.flush(configuration: config)
                precondition(migrated.deliveryState(eventID: "echo-event") == .delivered && migrated.pendingCount == 0)
            }
            await echoQueue.refreshReceipts(configuration: config, eventID: "echo-event")
            precondition(echoQueue.deliveryState(eventID: "echo-event") == .unknown, "bad echo confirmed delivery")
            confirmEcho = true
            await echoQueue.refreshReceipts(configuration: config, eventID: "echo-event")
            await echoQueue.flush(configuration: config)
            precondition(echoQueue.deliveryState(eventID: "echo-event") == .delivered && echoPosts == 1 && echoQueue.pendingCount == 0 && echoQueue.retainedInputs().isEmpty)
            let restoredEcho = InputBackupClient(directory: echoDir, credential: { actor }, send: { _ in fatalError("confirmed attributed receipt queried after restart") })
            try restoredEcho.configure(config)
            precondition(restoredEcho.deliveryState(eventID: "echo-event") == .delivered)
            await restoredEcho.refreshReceipts(configuration: config)
        }
        let upperDir = root.appendingPathComponent("retained-upper-bound")
        try FileManager.default.createDirectory(at: upperDir, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: receiptDir.appendingPathComponent("scope-salt"), to: upperDir.appendingPathComponent("scope-salt"))
        let upperReceipts = full.map { entry -> [String:Any] in var row = entry; row["state"] = "unknown"; return row }
        let upperPending = upperReceipts.map { ["eventID": $0["eventID"]!, "text": "synthetic context", "scope": $0["scope"]!] }
        try JSONSerialization.data(withJSONObject: upperReceipts).write(to: upperDir.appendingPathComponent("receipts-v1.json"))
        try JSONSerialization.data(withJSONObject: ["version":2,"items":upperPending]).write(to: upperDir.appendingPathComponent("pending.json"))
        let upperQueue = InputBackupClient(directory: upperDir, credential: { actor }, send: { _ in fatalError("upper-bound review network") })
        try upperQueue.configure(config)
        let upperStart = Date()
        let upperFirst = try upperQueue.retainedPage()
        let upperLast = try upperQueue.retainedPage(offset: 4980, snapshot: upperFirst.snapshot)
        precondition(upperFirst.total == 5000 && upperLast.items.count == 20 && upperLast.nextOffset == nil)
        precondition(upperQueue.canReview(upperLast.items[19]))
        print("OBSERVATION: synthetic5000 originals two pages plus selected-copy check elapsed=\(Date().timeIntervalSince(upperStart)) seconds; not GUI latency acceptance")
        let collisionDir = root.appendingPathComponent("retained-key-collision")
        try FileManager.default.createDirectory(at: collisionDir, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: receiptDir.appendingPathComponent("scope-salt"), to: collisionDir.appendingPathComponent("scope-salt"))
        var collisionReceipt = sample; collisionReceipt["scope"] = (sample["scope"] as! String) + ":suffix"; collisionReceipt["eventID"] = "poison"; collisionReceipt["state"] = "unknown"
        let collisionPending = ["eventID":"suffix:poison", "text":"synthetic context", "scope": sample["scope"]!]
        try JSONSerialization.data(withJSONObject: [collisionReceipt]).write(to: collisionDir.appendingPathComponent("receipts-v1.json"))
        try JSONSerialization.data(withJSONObject: ["version":2,"items":[collisionPending]]).write(to: collisionDir.appendingPathComponent("pending.json"))
        let collisionQueue = InputBackupClient(directory: collisionDir, credential: { actor }, send: { _ in fatalError("corrupt legacy review network") })
        try collisionQueue.configure(config)
        do { _ = try collisionQueue.retainedPage(); fatalError("invalid legacy event identifier accepted") } catch InputBackupError.storage {}
        var validDuplicate = upperPending[0]
        validDuplicate["eventID"] = "duplicated-event"
        try JSONSerialization.data(withJSONObject: ["version":2,"items":[validDuplicate,validDuplicate]]).write(to: collisionDir.appendingPathComponent("pending.json"))
        let duplicateQueue = InputBackupClient(directory: collisionDir, credential: { actor }, send: { _ in fatalError("duplicate original review network") })
        try duplicateQueue.configure(config)
        do { _ = try duplicateQueue.retainedPage(); fatalError("duplicate scope/event original accepted") } catch InputBackupError.storage {}
        let ambiguousOriginal = RetainedContextInput(id: "duplicated-event", text: "synthetic context", project: nil, state: .unknown, scope: sample["scope"] as? String)
        precondition(!duplicateQueue.canReview(ambiguousOriginal))
        let duplicateReceiptBytes = try JSONSerialization.data(withJSONObject: [upperReceipts[0],upperReceipts[0]])
        try duplicateReceiptBytes.write(to: collisionDir.appendingPathComponent("receipts-v1.json"))
        try JSONSerialization.data(withJSONObject: ["version":2,"items":[upperPending[0]]]).write(to: collisionDir.appendingPathComponent("pending.json"))
        let duplicateReceiptQueue = InputBackupClient(directory: collisionDir, credential: { actor }, send: { _ in fatalError("duplicate receipt review network") })
        try duplicateReceiptQueue.configure(config)
        do { _ = try duplicateReceiptQueue.retainedPage(); fatalError("duplicate receipt accepted") } catch InputBackupError.storage {}
        let unchangedDuplicateReceipt = try Data(contentsOf: collisionDir.appendingPathComponent("receipts-v1.json"))
        precondition(unchangedDuplicateReceipt == duplicateReceiptBytes)
        var pageCalls = 0
        var pageActor = actor
        let pageDir = root.appendingPathComponent("retained-paging")
        let pageQueue = InputBackupClient(directory: pageDir, credential: { pageActor }, send: { _ in
            pageCalls += 1; return (Data(#"{"detail":"PROJECT_DENIED"}"#.utf8), 400)
        })
        try pageQueue.configure(config)
        for index in 0..<41 { try pageQueue.enqueue(eventID: "page-\(index)", text: "original-\(index)", configuration: config) }
        let localFirst = try pageQueue.retainedPage()
        let localLast = try pageQueue.retainedPage(offset: 40, snapshot: localFirst.snapshot)
        precondition(localFirst.total == 41 && localLast.items[0].state == .local && pageQueue.canReview(localLast.items[0]) && pageCalls == 0, "receipt-less local originals lost or read sent HTTP")
        for _ in 0..<3 { await pageQueue.flush(configuration: config) }
        precondition(!pageQueue.canReview(localLast.items[0]), "old local state copied after receipt changed")
        do { _ = try pageQueue.retainedPage(offset: 20); fatalError("unanchored later page accepted") } catch InputBackupError.conflict {}
        let firstPage = try pageQueue.retainedPage()
        let secondPage = try pageQueue.retainedPage(offset: 20, snapshot: firstPage.snapshot)
        let lastPage = try pageQueue.retainedPage(offset: 40, snapshot: firstPage.snapshot)
        precondition(firstPage.items.count == 20 && firstPage.total == 41 && firstPage.nextOffset == 20)
        precondition(secondPage.items.count == 20 && secondPage.nextOffset == 40)
        precondition(lastPage.items.count == 1 && lastPage.nextOffset == nil && lastPage.items[0].id == "page-40")
        precondition(Set((firstPage.items + secondPage.items + lastPage.items).map(\.id)).count == 41)
        precondition(pageQueue.canReview(lastPage.items[0]), "copy from later page blocked")
        let forged = RetainedContextInput(id: lastPage.items[0].id, text: "forged", project: nil, state: .rejected, scope: lastPage.items[0].scope)
        precondition(!pageQueue.canReview(forged))
        let otherScopeRow = RetainedContextInput(id: lastPage.items[0].id, text: lastPage.items[0].text, project: nil, state: .rejected, scope: "another-scope")
        precondition(otherScopeRow.reviewKey != lastPage.items[0].reviewKey && !pageQueue.canReview(otherScopeRow))
        for offset in [-20, 1, 60, Int.max] {
            do { _ = try pageQueue.retainedPage(offset: offset); fatalError("invalid page offset") } catch InputBackupError.conflict {}
        }
        let restartedPages = InputBackupClient(directory: pageDir, credential: { pageActor }, send: { _ in fatalError("paging performed HTTP") })
        try restartedPages.configure(config)
        let restartedPage = try restartedPages.retainedPage(offset: 20, snapshot: firstPage.snapshot)
        precondition(restartedPage.items == secondPage.items)
        pageActor = try InputBackupClient.bindCredential("another-page-actor", to: config.endpoint)
        do { _ = try pageQueue.retainedPage(offset: 20, snapshot: firstPage.snapshot); fatalError("page crossed actor") } catch InputBackupError.conflict {}
        precondition(!pageQueue.canReview(lastPage.items[0]))
        pageActor = actor
        try pageQueue.enqueue(eventID: "page-new", text: "new rejected original", configuration: config)
        await pageQueue.flush(configuration: config)
        do { _ = try pageQueue.retainedPage(offset: 20, snapshot: firstPage.snapshot); fatalError("changed snapshot silently skipped rows") } catch InputBackupError.conflict {}
        let changedPage = try pageQueue.retainedPage()
        precondition(changedPage.total == 42)
        precondition(pageCalls == 42, "read-only paging sent requests")
        var dualActor = try InputBackupClient.bindCredential("dual-legacy-token", to: config.endpoint)
        let dualQueue = InputBackupClient(directory: root.appendingPathComponent("dual-scopes"), credential: { dualActor }, send: { _ in (Data(#"{"detail":"PROJECT_DENIED"}"#.utf8), 400) })
        try dualQueue.configure(config)
        try dualQueue.enqueue(eventID: "same-event", text: "legacy original", configuration: config)
        await dualQueue.flush(configuration: config)
        dualActor = String(decoding: try JSONEncoder().encode(["token":"vox_sso_dual", "origin":config.endpoint.absoluteString, "scopeIdentity":"verified-dual"]), as: UTF8.self)
        try dualQueue.enqueue(eventID: "same-event", text: "current original", configuration: config)
        await dualQueue.flush(configuration: config)
        dualActor = String(decoding: try JSONEncoder().encode(["token":"vox_sso_dual", "origin":config.endpoint.absoluteString, "scopeIdentity":"verified-dual", "legacyToken":"dual-legacy-token"]), as: UTF8.self)
        let dualPage = try dualQueue.retainedPage()
        precondition(dualPage.items.count == 2 && Set(dualPage.items.map(\.id)).count == 1 && Set(dualPage.items.map(\.reviewKey)).count == 2)
        precondition(dualPage.items.allSatisfy { dualQueue.canReview($0) }, "scope-specific originals lost or copy unavailable")
        print("PASS: stable 20-row retained pages, later-page copy scope validation, restart, queue/actor invalidation and zero paging HTTP")
        print("PASS: missing/wrong/unsupported/boolean project echo stays unknown without repost; exact GET echo confirms delivery and survives restart")
        print("PASS: validated GET catalog, stale-account rejection, unknown/draft freeze, project snapshot survives restart and cannot be rebound")
        print("PASS: 20 retained blocked inputs do not starve the 21st; pause during renewal prevents POST")
        for status in [400, 413, 422, 409, 403] {
            var poisonPosts: [String] = []
            let poison = InputBackupClient(directory: root.appendingPathComponent("poison-\(status)"), credential: { actor }, send: { request in
                let event = (try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String])["eventId"]!
                poisonPosts.append(event)
                if event == "poison" { return (Data(#"{"detail":"PROJECT_DENIED"}"#.utf8), status) }
                return (Data(#"{"eventId":"valid","status":"pending"}"#.utf8), 202)
            })
            try poison.configure(config)
            try poison.enqueue(eventID: "poison", text: "keep rejected original", configuration: config)
            try poison.enqueue(eventID: "valid", text: "send valid later input", configuration: config)
            await poison.flush(configuration: config)
            await poison.flush(configuration: config)
            precondition(poisonPosts == ["poison", "valid"])
            precondition(poison.deliveryState(eventID: "poison") == (status == 409 ? .conflict : .rejected))
            precondition(poison.deliveryState(eventID: "valid") == .pending)
            precondition(poison.pendingCount == 1, "rejected/conflicting original must remain local")
            precondition(poison.retainedInputs().count == 1 && poison.retainedInputs()[0].text == "keep rejected original")
            if status == 409 {
                await poison.refreshReceipts(configuration: config, eventID: "poison")
                precondition(poisonPosts == ["poison", "valid"], "conflicting text cannot be reconciled with status-only GET")
            }
        }
        var deniedEvents: [String] = []
        let denied = InputBackupClient(directory: root.appendingPathComponent("target-denied"), credential: { actor }, send: { request in
            let event = (try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String])["eventId"]!
            deniedEvents.append(event)
            return (Data(#"{"detail":"TARGET_DENIED"}"#.utf8), 403)
        })
        try denied.configure(config)
        try denied.enqueue(eventID: "first", text: "first", configuration: config)
        try denied.enqueue(eventID: "second", text: "second", configuration: config)
        await denied.flush(configuration: config)
        precondition(deniedEvents == ["first"] && denied.pendingCount == 2)
        print("PASS: project denial isolates only the item; target denial stops the pass")
        print("PASS: definitive 400/413/422 and ambiguous 409 do not starve valid input or trigger repost")
        let faultDir = root.appendingPathComponent("receipt-write-fault")
        var faultPosts = 0
        let fault = InputBackupClient(directory: faultDir, credential: { actor }, send: { request in
            faultPosts += 1
            precondition(request.httpMethod == "POST")
            return (Data("{\"eventId\":\"write-fault\",\"status\":\"pending\"}".utf8), 202)
        })
        try fault.configure(config)
        try fault.enqueue(eventID: "write-fault", text: "preserve original", configuration: config)
        // A directory at the destination forces atomic rename failure even as root.
        let obstruction = faultDir.appendingPathComponent("receipts-v1.json")
        try FileManager.default.createDirectory(at: obstruction, withIntermediateDirectories: false)
        await fault.flush(configuration: config)
        precondition(faultPosts == 1 && fault.pendingCount == 1)
        precondition(fault.deliveryState(eventID: "write-fault") == .local)
        let faultQueue = try Data(contentsOf: faultDir.appendingPathComponent("pending.json"))
        precondition(String(decoding: faultQueue, as: UTF8.self).contains("preserve original"))
        let faultFiles = try FileManager.default.contentsOfDirectory(atPath: faultDir.path)
        precondition(!faultFiles.contains { $0.hasPrefix("receipt-") && $0.hasSuffix(".tmp") })
        try FileManager.default.removeItem(at: obstruction) // Generated fixture only.
        await fault.flush(configuration: config)
        precondition(faultPosts == 2 && fault.pendingCount == 0)
        precondition(fault.deliveryState(eventID: "write-fault") == .pending)
        print("PASS: atomic receipt write failure preserves original and cleans staging; retry requires server idempotency")
        print("PASS: bounded 20-query passes rotate across 25 events")
        print("PASS: crash acknowledgement recovery, legacy receipt continuity, capacity rejection before network, invalid acceptance no repost")
        print("PASS: durable per-event receipts, GET-only recovery, unknown no-resend, stale actor response isolation, payload conflict, delivered restart and private metadata")
        print("PASS: empty queue skips Keychain; durable replay, conflict, private storage, no stored credential, identity isolation, wrong receipt, timeout retention, accepted receipt, URL validation, corruption retention, pause during flush, equivalent URL, blocked retention")
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vox-backup-check-') as temporary:
        work = Path(temporary)
        (work / 'Dependencies.swift').write_text(STUB)
        # Throwing calls cannot be evaluated inside precondition's nonthrowing autoclosure.
        harness = HARNESS.replace('precondition(try Data(contentsOf: corrupt.appendingPathComponent("pending.json")) == Data("broken".utf8))',
                                  'let preserved = try Data(contentsOf: corrupt.appendingPathComponent("pending.json")); precondition(preserved == Data("broken".utf8))')
        (work / 'Check.swift').write_text(harness)
        executable = work / 'check'
        subprocess.run(['swiftc', '-sdk', args.sdk, '-swift-version', '5', '-parse-as-library',
                        str(root / 'macos/VoxLog/Services/InputBackupClient.swift'),
                        str(work / 'Dependencies.swift'), str(work / 'Check.swift'),
                        '-o', str(executable)], check=True)
        subprocess.run([str(executable), str(work / 'queue')], check=True)


if __name__ == '__main__':
    main()
