#!/usr/bin/env python3
"""Exercise real private inbox/client using isolated synthetic responses; no tenant/Keychain."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from verify_input_backup_client import STUB

HARNESS = r'''
import Foundation
import CryptoKit
import Darwin
@main struct Check {
 @MainActor static func main() async throws {
  var assertionCount = 0
  func expect(_ value: Bool, line: Int = #line) { assertionCount += 1; if !value { preconditionFailure("synthetic assertion at line \(line)") } }
  let root = URL(fileURLWithPath: CommandLine.arguments[1])
  let fixture = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))) as! [String: Any]
  var source = fixture["source"] as! [String: Any]
  let stamp = ISO8601DateFormatter().string(from: Date())
  source["createdAt"] = stamp; source["revisionChangedAt"] = stamp
  func bytes(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
  func page(_ items: [[String: Any]], more: Bool = false, cursor: String? = nil) throws -> Data {
   try bytes(["version": 1, "scope": "own-private-context", "items": items, "hasMore": more,
              "nextCursor": cursor as Any? ?? NSNull(), "omittedRecords": false, "evidenceKind": "user-statement"])
  }
  var gate = fixture["references"] as! [String: Any]; gate["valid"] = true
  let validGate = try bytes(gate)
  let transition = ContextPrivateInbox()
  transition.setInput("synthetic owner A", scope: { "A" })
  expect(!transition.reconcileDisplayIdentity(previous: nil, current: "A") && !transition.input.isEmpty)
  expect(transition.reconcileDisplayIdentity(previous: nil, current: "B") && transition.input.isEmpty)
  transition.setInput("synthetic owner A", scope: { "A" })
  expect(transition.reconcileDisplayIdentity(previous: "A", current: nil) && transition.input.isEmpty)
  expect(!transition.reconcileDisplayIdentity(previous: nil, current: "B") && transition.input.isEmpty)
  let inbox = ContextPrivateInbox()
  var identity = "actor-a", calls = 0
  let read: ContextPrivateInbox.Request = { path, method, body in
   calls += 1; expect(path == "v1/context-private-search" && method == "POST")
   let request = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
   expect(request["q"] as? String == "parser" && request["tag"] as? String == "question")
   return try page([source])
  }
  inbox.query = "parser"; inbox.filterTag = "question"
  await inbox.load(request: read, scope: { identity })
  expect(!inbox.failed && inbox.sources.count == 1 && calls == 1)
  await inbox.reviewDraft(create: true, request: { path, method, body in
   expect(path == "v1/context-private-reference-check" && method == "POST")
   let request = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
   let refs = request["sources"] as! [[String: Any]]
   expect(refs.count == 1 && refs[0]["revision"] as? Int == 1)
   return validGate
  }, scope: { identity })
  expect(inbox.hasDraft && inbox.draftValid && inbox.draft.contains("不证明工作完成"))
  inbox.input = "未提交私人输入"
  inbox.draft = "本人编辑"
  await inbox.reviewDraft(create: false, request: { _, _, _ in validGate }, scope: { identity })
  expect(inbox.draft == "本人编辑")
  await inbox.reviewDraft(create: false, request: { _, _, _ in try bytes(fixture["references"]!) }, scope: { identity })
  expect(inbox.failed && !inbox.hasDraft && inbox.draft.isEmpty && inbox.sources.isEmpty && !inbox.hasMore && inbox.pages == 0)
  expect(inbox.hasUnsubmittedRecovery && inbox.input.isEmpty && !inbox.canStageSavedRequest)
  identity = "other-recovery-actor"
  await inbox.recoverUnsubmitted(request: { _, _, _ in preconditionFailure("cross-actor recovery transport") }, scope: { identity })
  expect(inbox.hasUnsubmittedRecovery && inbox.input.isEmpty)
  identity = "actor-a"
  await inbox.recoverUnsubmitted(request: { path, method, body in
    expect(path == "v1/context-private-authorization" && method == "GET" && body == nil)
    return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"])
  }, scope: { identity })
  expect(inbox.input == "未提交私人输入" && !inbox.hasUnsubmittedRecovery)

  inbox.reset(); inbox.query = "parser"; inbox.filterTag = "question"
  await inbox.load(request: read, scope: { identity })
  inbox.select(inbox.sources[0]); inbox.input = "revised"
  await inbox.mutate(request: { path, method, body in
   expect(path.hasSuffix(source["id"] as! String) && method == "PUT")
   let request = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
   expect(request["expectedRevision"] as? Int == 1 && request["text"] as? String == "revised")
   expect(!inbox.hasDraft)
   var result = source; result["revision"] = 2; result["text"] = "revised"
   return try bytes(result)
  }, scope: { identity })
  expect(!inbox.writeUncertain && inbox.input.isEmpty && inbox.sources.isEmpty)
  inbox.newInput(); inbox.input = "private original"; calls = 0
  var uncertainBody: Data?
  await inbox.mutate(request: { path, method, body in
   calls += 1; uncertainBody = body; expect(path == "v1/context-private" && method == "POST")
   let payload = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
   expect(payload["visibility"] as? String == "private" && payload["projectId"] == nil)
   throw ContextPrivateInbox.Failure.invalid
  }, scope: { identity })
  expect(calls == 1 && inbox.writeUncertain && inbox.input == "private original")
  await inbox.mutate(request: { _, _, _ in preconditionFailure("ambiguous write retried") }, scope: { identity })
  expect(calls == 1)
  identity = "different-actor"
  await inbox.retryUncertain(request: { _, _, _ in preconditionFailure("cross-actor retry sent") }, scope: { identity })
  expect(inbox.writeUncertain)
  identity = "actor-a"
  await inbox.load(request: { _, _, _ in try page([]) }, scope: { identity })
  expect(inbox.writeUncertain) // Filtered/empty reload does not resolve write ambiguity.
  await inbox.retryUncertain(request: { path, method, body in
   expect(body == nil && path.hasPrefix("v1/context-private-events/") && method == "GET")
   let payload = try JSONSerialization.jsonObject(with: uncertainBody!) as! [String: Any]
   var result = source; result["eventId"] = payload["eventId"]; result["text"] = payload["text"]; result["tags"] = payload["tags"]
   return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": result, "mutationId": NSNull()])
  }, scope: { identity })
  expect(!inbox.writeUncertain && inbox.input.isEmpty)
  inbox.reset(); inbox.query = "parser"; inbox.filterTag = "question"
  await inbox.load(request: read, scope: { identity })
  inbox.select(inbox.sources[0]); inbox.input = "" // Withdrawal works without usable text/capacity.
  await inbox.mutate(withdraw: true, request: { path, method, body in
   expect(path.hasSuffix("/withdraw") && method == "POST")
   let request = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
   expect(Set(request.keys) == ["expectedRevision", "requestId"])
   expect(ContextWeeklySource.identifier(request["requestId"] as! String, maximum: 128))
   var terminal = fixture["withdrawn"] as! [String: Any]
   terminal["createdAt"] = stamp; terminal["revisionChangedAt"] = stamp
   return try bytes(terminal)
  }, scope: { identity })
  expect(!inbox.writeUncertain && inbox.sources.isEmpty)
  inbox.reset(); inbox.query = "parser"; inbox.filterTag = "question"
  await inbox.load(request: read, scope: { identity })
  inbox.select(inbox.sources[0])
  await inbox.load(request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { identity })
  expect(inbox.editing == nil && inbox.input.isEmpty && inbox.withdrawalReference != nil)
  await inbox.mutate(withdraw: true, request: { path, _, _ in
   expect(path.hasSuffix("/withdraw")); var terminal = fixture["withdrawn"] as! [String: Any]
   terminal["createdAt"] = stamp; terminal["revisionChangedAt"] = stamp; return try bytes(terminal)
  }, scope: { identity })
  expect(!inbox.writeUncertain && inbox.withdrawalReference == nil)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { identity })
  await inbox.reviewDraft(create: true, request: { _, _, _ in inbox.invalidateDraft(); return validGate }, scope: { identity })
  expect(!inbox.hasDraft && !inbox.draftValid && inbox.draft.isEmpty)
  await inbox.reviewDraft(create: true, request: { _, _, _ in inbox.suspend(); return validGate }, scope: { identity })
  expect(!inbox.hasDraft && inbox.sources.isEmpty && inbox.input.isEmpty)
  inbox.query = "private search"; inbox.filterTag = "question"; inbox.suspend()
  expect(inbox.query.isEmpty && inbox.filterTag.isEmpty)
  inbox.reset(); inbox.input = "valid"; inbox.inputTag = "invalid-tag"
  await inbox.mutate(request: { _, _, _ in preconditionFailure("invalid local body sent") }, scope: { identity })
  expect(inbox.failed && !inbox.writeUncertain)
  let editorBefore = inbox.inputEditorEpoch
  inbox.suspend(); expect(inbox.inputEditorEpoch > editorBefore)
  inbox.setQuery("previous query"); inbox.setQuery(String(repeating: "q", count: 129))
  expect(inbox.query == "previous query" && inbox.queryLimit)
  inbox.chooseProject("", scope: { identity }); inbox.setInput("previous"); inbox.chooseProject("", scope: { identity }); inbox.setInput(String(repeating: "😀", count: 8193))
  expect(inbox.input == "previous" && inbox.inputLimit)
  inbox.setDraft("previous draft"); inbox.setDraft(String(repeating: "x", count: 300001))
  expect(inbox.draft == "previous draft" && inbox.draftLimit)
  inbox.discardUncertain(); expect(inbox.input.isEmpty && inbox.draft.isEmpty && !inbox.writeUncertain)
  inbox.reset()
  await inbox.load(request: { _, _, _ in inbox.reset(); return try page([source]) }, scope: { identity })
  expect(inbox.sources.isEmpty && !inbox.failed)
  inbox.reset()
  await inbox.load(request: { _, _, _ in identity = "actor-b"; return try page([source]) }, scope: { identity })
  expect(inbox.failed && inbox.sources.isEmpty)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source], more: true, cursor: "next") }, scope: { identity })
  inbox.select(inbox.sources[0]); inbox.input = "unsaved revision"
  await inbox.mutate(request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { identity })
  expect(inbox.sources.isEmpty && inbox.pages == 0 && !inbox.hasMore && inbox.writeUncertain)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { identity })
  inbox.chooseProject("", scope: { identity }); inbox.setInput("volatile new input")
  await inbox.reviewDraft(create: true, request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { identity })
  expect(inbox.hasUnsubmittedRecovery && inbox.input.isEmpty)
  await inbox.recoverUnsubmitted(request: { path, method, body in
    expect(path == "v1/context-private-authorization" && method == "GET" && body == nil)
    inbox.chooseProject("", scope: { identity }); inbox.setInput("new typing during volatile recovery")
    return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"])
  }, scope: { identity })
  expect(inbox.input == "new typing during volatile recovery" && inbox.hasUnsubmittedRecovery)
  await inbox.mutate(request: { _, _, _ in fatalError("hidden recovery lineage was submitted as new text") }, scope: { identity })
  await inbox.recoverUnsubmitted(request: { _, _, _ in fatalError("nonempty editor was overwritten") }, scope: { identity })
  inbox.chooseProject("", scope: { identity }); inbox.setInput("")
  await inbox.recoverUnsubmitted(request: { _, _, _ in inbox.inputTag = "decision"; return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"]) }, scope: { identity })
  expect(inbox.input.isEmpty && inbox.inputTag == "decision" && inbox.hasUnsubmittedRecovery)
  inbox.inputTag = ""
  await inbox.recoverUnsubmitted(request: { _, _, _ in inbox.suspend(); return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"]) }, scope: { identity })
  expect(inbox.input.isEmpty && inbox.hasUnsubmittedRecovery)
  await inbox.recoverUnsubmitted(request: { _, method, body in expect(method == "GET" && body == nil); return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"]) }, scope: { identity })
  expect(inbox.input == "volatile new input" && !inbox.hasUnsubmittedRecovery)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { identity })
  inbox.inputTag = "verification"
  await inbox.reviewDraft(create: true, request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { identity })
  expect(inbox.hasUnsubmittedRecovery && inbox.input.isEmpty && inbox.inputTag.isEmpty)
  await inbox.recoverUnsubmitted(request: { _, method, body in expect(method == "GET" && body == nil); return try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"]) }, scope: { identity })
  expect(inbox.input.isEmpty && inbox.inputTag == "verification" && !inbox.hasUnsubmittedRecovery)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { identity })
  inbox.select(inbox.sources[0]); inbox.input = "unsaved old-revision edit"
  await inbox.reviewDraft(create: true, request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { identity })
  expect(inbox.hasUnsubmittedRecovery && inbox.editing == nil && inbox.input.isEmpty)
  await inbox.mutate(request: { _, _, _ in preconditionFailure("unvalidated recovery saved") }, scope: { identity })
  await inbox.recoverUnsubmitted(request: { _, _, _ in var changed = source; changed["revision"] = 2; return try bytes(changed) }, scope: { identity })
  expect(inbox.hasUnsubmittedRecovery && inbox.input.isEmpty)
  await inbox.recoverUnsubmitted(request: { path, method, _ in expect(method == "GET" && path.hasSuffix(source["id"] as! String)); return try bytes(source) }, scope: { identity })
  expect(!inbox.hasUnsubmittedRecovery && inbox.input == "unsaved old-revision edit" && inbox.editing?.revision == 1)
  inbox.reset()
  var bad = source; bad["evidenceKind"] = "verified-code"
  await inbox.load(request: { _, _, _ in try page([bad]) }, scope: { identity })
  expect(inbox.failed)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([], more: true, cursor: "next") }, scope: { identity })
  expect(inbox.hasMore && inbox.sources.isEmpty)
  await inbox.load(more: true, request: { _, _, _ in try page([source]) }, scope: { identity })
  expect(inbox.sources.count == 1 && inbox.pages == 2)
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([], more: true, cursor: "next") }, scope: { identity })
  await inbox.load(more: true, request: { _, _, _ in try page([], more: true, cursor: "next") }, scope: { identity })
  expect(inbox.failed && inbox.sources.isEmpty)
  for badKey in ["actorId", "textIsVerified"] {
   var bad = source; bad[badKey] = "injected"
   expect((try? ContextPrivateSource.decode(bytes(bad))) == nil)
  }
  var terminal = fixture["withdrawn"] as! [String: Any]; terminal["text"] = "leaked"
  expect((try? ContextPrivateSource.decode(bytes(terminal))) == nil)
  expect(ContextPrivateSource.validText(String(repeating: "😀", count: 8192)))
  expect(!ContextPrivateSource.validText("bad\0text"))
  inbox.reset()
  func largePage(_ start: Int, more: Bool) throws -> Data {
   let rows = (start..<(start+20)).map { number -> [String: Any] in
    var row = source; row["id"] = "source-\(number)"; row["eventId"] = "event-\(number)"; row["text"] = String(repeating: "x", count: 8192); return row
   }
   return try page(rows, more: more, cursor: more ? "large-next" : nil)
  }
  await inbox.load(request: { _, _, _ in try largePage(0, more: true) }, scope: { identity })
  await inbox.load(more: true, request: { _, _, _ in try largePage(20, more: false) }, scope: { identity })
  await inbox.reviewDraft(create: true, request: { _, _, _ in preconditionFailure("oversize draft called gate") }, scope: { identity })
  expect(inbox.draftTooLarge && inbox.sources.count == 40 && !inbox.failed)
  inbox.reset(); inbox.chooseProject("", scope: { identity }); inbox.input = "in-flight original"
  var releaseWrite: CheckedContinuation<Data, Never>?
  var pinned: [String: Any]?
  let inFlight = Task { @MainActor in
   await inbox.mutate(request: { _, _, body in
    pinned = try JSONSerialization.jsonObject(with: body!) as? [String: Any]
    return await withCheckedContinuation { releaseWrite = $0 }
   }, scope: { identity })
  }
  while releaseWrite == nil { await Task.yield() }
  expect(inbox.writeInFlight)
  inbox.suspend()
  await inbox.retryUncertain(request: { _, _, _ in preconditionFailure("original raced retry") }, scope: { identity })
  var ack = source; ack["eventId"] = pinned!["eventId"]; ack["text"] = pinned!["text"]; ack["tags"] = pinned!["tags"]
  releaseWrite!.resume(returning: try bytes(ack)); await inFlight.value
  expect(!inbox.writeInFlight && inbox.writeUncertain)
  await inbox.retryUncertain(request: { _, method, body in
   expect(method == "GET" && body == nil)
   return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": ack, "mutationId": NSNull()])
  }, scope: { identity })
  expect(!inbox.writeUncertain)
  let config = try InputBackupConfiguration(endpoint: "https://private.example.test", targetID: "archive")
  var credential = try InputBackupClient.bindCredential("actor-a", to: config.endpoint)
  var switchActor = false, transportCalls = 0
  let client = InputBackupClient(directory: root, credential: { credential }, send: { req in
   transportCalls += 1
   expect(req.url!.host == "private.example.test" && req.url!.query == nil)
   expect(req.value(forHTTPHeaderField: "Authorization") == "Bearer actor-a")
   if req.url?.path == "/v1/context-private-authorization" {
    expect(req.httpMethod == "GET" && req.httpBody == nil)
   }
   if switchActor { credential = try InputBackupClient.bindCredential("actor-b", to: config.endpoint) }
   return (try page([source]), 200)
  })
  try client.configure(config)
  let displaySaltFile = root.appendingPathComponent("scope-salt")
  let displaySaltExisted = FileManager.default.fileExists(atPath: displaySaltFile.path)
  expect(client.attributionScopeForDisplay() == nil)
  expect(FileManager.default.fileExists(atPath: displaySaltFile.path) == displaySaltExisted && transportCalls == 0)
  _ = try await client.privateContextRequest(path: "v1/context-private-search", method: "POST", body: bytes(["q":"private words"]))
  expect(client.pendingCount == 0)
  let displaySaltBytes = try Data(contentsOf: displaySaltFile)
  expect(client.attributionScopeForDisplay() == (try client.attributionScope()))
  expect(try Data(contentsOf: displaySaltFile) == displaySaltBytes && transportCalls == 1)
  var interactiveReads = 0
  let deniedDisplay = InputBackupClient(directory: root, credential: { interactiveReads += 1; return credential }, displayCredential: { nil }, send: { _ in preconditionFailure("display transport") })
  try deniedDisplay.configure(config)
  interactiveReads = 0
  expect(deniedDisplay.attributionScopeForDisplay() == nil && interactiveReads == 0)
  var displayIdentity: String? = credential
  let displaySwitch = InputBackupClient(directory: root, credential: { interactiveReads += 1; return credential }, displayCredential: { displayIdentity }, send: { _ in preconditionFailure("display identity transport") })
  try displaySwitch.configure(config)
  interactiveReads = 0
  let displayA = displaySwitch.attributionScopeForDisplay()
  expect(displayA != nil)
  displayIdentity = try InputBackupClient.bindCredential("actor-other-display", to: config.endpoint)
  expect(displaySwitch.attributionScopeForDisplay() != displayA)
  displayIdentity = nil
  expect(displaySwitch.attributionScopeForDisplay() == nil)
  expect(try Data(contentsOf: displaySaltFile) == displaySaltBytes && interactiveReads == 0)
  for path in ["v1/input-backups", "v1/context-private/../../auth", "https://evil.test", "v1/context-private-search?q=leak"] {
   do { _ = try await client.privateContextRequest(path: path, method: "POST", body: nil); preconditionFailure("invalid private path") } catch {}
  }
  expect(transportCalls == 1)
  _ = try await client.privateContextRequest(path: "v1/context-private-authorization", method: "GET", body: nil)
  expect(transportCalls == 2 && client.pendingCount == 0)
  for method in ["POST", "PUT", "DELETE"] {
   do { _ = try await client.privateContextRequest(path: "v1/context-private-authorization", method: method, body: nil); fatalError("authorization mutation transported") } catch {}
  }
  do { _ = try await client.privateContextRequest(path: "v1/context-private-authorization", method: "GET", body: Data("private input".utf8)); fatalError("authorization body transported") } catch {}
  expect(transportCalls == 2)
  switchActor = true
  do { _ = try await client.privateContextRequest(path: "v1/context-private-search", method: "POST", body: nil); preconditionFailure("actor changed during I/O") } catch {}
  expect(transportCalls == 3 && client.pendingCount == 0)

  let vaultRoot = root.appendingPathComponent("encrypted-vault")
  var key: String?, keyReads = 0, keyWrites = 0
  let vault = ContextPrivateDraftStore(directory: vaultRoot, readKey: { keyReads += 1; return key }, saveKey: { keyWrites += 1; key = $0 })
  expect(keyReads == 0 && keyWrites == 0 && !FileManager.default.fileExists(atPath: vaultRoot.path))
  let owner = String(repeating: "a", count: 64), otherOwner = String(repeating: "b", count: 64)
  let saved = ContextPrivateSavedDraft(version: 1, scope: owner, text: "private durable text", references: [.init(id: source["id"] as! String, revision: 1)], savedAt: stamp)
  try vault.save(saved)
  let file = vaultRoot.appendingPathComponent(owner + ".v1.aes")
  let originalCipher = try Data(contentsOf: file)
  expect(keyWrites == 1 && !(String(data: originalCipher, encoding: .utf8)?.contains(saved.text) ?? false))
  expect((try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as! NSNumber).intValue == 0o600)
  expect((try FileManager.default.attributesOfItem(atPath: vaultRoot.path)[.posixPermissions] as! NSNumber).intValue == 0o700)
  expect(try vault.load(scope: owner).text == saved.text)
  try vault.save(saved); expect(try Data(contentsOf: file) == originalCipher) // Idempotent retry reflushes without resealing.
  let changedDraft = ContextPrivateSavedDraft(version: 1, scope: owner, text: "different draft", references: saved.references, savedAt: stamp)
  try vault.save(changedDraft); expect(try Data(contentsOf: file) != originalCipher)
  try vault.save(saved)
  let preserved = try Data(contentsOf: file)
  let failedVault = ContextPrivateDraftStore(directory: vaultRoot, readKey: { key }, saveKey: { _ in preconditionFailure("replaced key") }, replace: { _, _ in false })
  do { try failedVault.save(changedDraft); preconditionFailure("replace failure ignored") } catch {}
  expect(try Data(contentsOf: file) == preserved)
  expect(!(try FileManager.default.contentsOfDirectory(atPath: vaultRoot.path)).contains(where: { $0.hasPrefix(".draft-") }))
  let oldKey = key; key = nil
  do { _ = try vault.load(scope: owner); preconditionFailure("missing key restore") } catch {}
  do { try vault.save(saved); preconditionFailure("missing key replaced ciphertext") } catch {}
  expect((try Data(contentsOf: file)) == preserved && keyWrites == 1)
  key = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0).base64EncodedString() }
  do { _ = try vault.load(scope: owner); preconditionFailure("wrong key decrypted") } catch {}
  do { try vault.save(saved); preconditionFailure("wrong key overwrote older cipher") } catch {}
  expect(try Data(contentsOf: file) == preserved)
  key = oldKey
  // A held OS-level lease blocks another store before key/file mutation.
  let lease = open(vaultRoot.appendingPathComponent(".vault.lock").path, O_RDWR)
  expect(lease >= 0 && flock(lease, LOCK_EX | LOCK_NB) == 0)
  do { try vault.save(saved); preconditionFailure("concurrent process lease ignored") } catch {}
  expect(try Data(contentsOf: file) == preserved)
  close(lease)
  var malformed = try JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as! [String: Any]
  for change in ["version", "extra"] {
   if change == "version" { malformed["version"] = 2 } else { malformed["version"] = 1; malformed["extra"] = "unexpected" }
   let encrypted = try AES.GCM.seal(bytes(malformed), using: SymmetricKey(data: Data(base64Encoded: key!)!), authenticating: Data(("vox-private-draft/v1|" + owner).utf8)).combined!
   try encrypted.write(to: file)
   do { _ = try vault.load(scope: owner); preconditionFailure("invalid encrypted schema accepted") } catch {}
  }
  try preserved.write(to: file)
  var unexpectedWrites = 0
  let lockedRoot = root.appendingPathComponent("locked-key")
  let lockedVault = ContextPrivateDraftStore(directory: lockedRoot, readKey: { throw ContextPrivateDraftStore.Failure.unavailable }, saveKey: { _ in unexpectedWrites += 1 })
  do { try lockedVault.save(saved); preconditionFailure("locked read created key") } catch {}
  expect(unexpectedWrites == 0)
  let deniedRoot = root.appendingPathComponent("denied-key")
  let deniedVault = ContextPrivateDraftStore(directory: deniedRoot, readKey: { nil }, saveKey: { _ in throw ContextPrivateDraftStore.Failure.unavailable })
  do { try deniedVault.save(saved); preconditionFailure("denied key persisted text") } catch {}
  expect(!(try FileManager.default.contentsOfDirectory(atPath: deniedRoot.path)).contains(where: { $0.hasSuffix(".aes") || $0.hasPrefix(".draft-") }))
  let copied = vaultRoot.appendingPathComponent(otherOwner + ".v1.aes")
  try preserved.write(to: copied); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: copied.path)
  do { _ = try vault.load(scope: otherOwner); preconditionFailure("scope substitution decrypted") } catch {}
  var tampered = preserved; tampered[tampered.count-1] ^= 1; try tampered.write(to: file)
  do { _ = try vault.load(scope: owner); preconditionFailure("tampered cipher decrypted") } catch {}
  try preserved.write(to: file)
  let invalid = ContextPrivateSavedDraft(version: 2, scope: owner, text: "invalid version", references: saved.references, savedAt: stamp)
  do { try vault.save(invalid); preconditionFailure("unknown schema saved") } catch {}
  expect(try Data(contentsOf: file) == preserved)
  try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: file.path)
  do { _ = try vault.load(scope: owner); preconditionFailure("public-readable cipher accepted") } catch {}
  do { try vault.save(saved); preconditionFailure("unsafe destination overwritten") } catch {}
  try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
  let symlinkRoot = root.appendingPathComponent("symlink-vault")
  try FileManager.default.createSymbolicLink(atPath: symlinkRoot.path, withDestinationPath: vaultRoot.path)
  let linked = ContextPrivateDraftStore(directory: symlinkRoot, readKey: { key }, saveKey: { _ in preconditionFailure("symlink generated key") })
  do { _ = try linked.load(scope: owner); preconditionFailure("symlink directory read") } catch {}
  let alias = vaultRoot.appendingPathComponent("alias")
  expect(link(file.path, alias.path) == 0)
  do { _ = try vault.load(scope: owner); preconditionFailure("hard link accepted") } catch {}
  unlink(alias.path)

  // First save may not report durability through an unflushed bootstrap ancestor, including retries.
  var bootstrapDraftKeyWrites = 0
  let draftBootstrapRoot = root.appendingPathComponent("draft-bootstrap/Drafts")
  let failedBootstrapDraft = ContextPrivateDraftStore(directory: draftBootstrapRoot, readKey: { nil }, saveKey: { _ in bootstrapDraftKeyWrites += 1 }, syncParent: { fd in
    var checkedPath = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    guard fcntl(fd, F_GETPATH, &checkedPath) == 0 else { return false }
    return !String(cString: checkedPath).hasSuffix("/queue") && fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
  })
  for _ in 0..<2 { do { try failedBootstrapDraft.save(saved); fatalError("draft bootstrap durability bypassed") } catch {} }
  expect(bootstrapDraftKeyWrites == 0 && !FileManager.default.fileExists(atPath: draftBootstrapRoot.appendingPathComponent(owner + ".v1.aes").path))
  let intermediateDraftRoot = root.appendingPathComponent("draft-intermediate-link")
  try FileManager.default.createSymbolicLink(at: intermediateDraftRoot, withDestinationURL: vaultRoot)
  let unsafeDraft = ContextPrivateDraftStore(directory: intermediateDraftRoot.appendingPathComponent("Drafts"), readKey: { key }, saveKey: { _ in fatalError("unsafe draft key write") })
  do { try unsafeDraft.save(saved); fatalError("draft intermediate symlink accepted") } catch {}
  do { _ = try unsafeDraft.load(scope: owner); fatalError("draft intermediate symlink read") } catch {}
  expect(!FileManager.default.fileExists(atPath: vaultRoot.appendingPathComponent("Drafts").path))
  // Fail the parent flush after rename; retry cannot bypass the failure merely because bytes are visible.
  let flushVault = ContextPrivateDraftStore(directory: vaultRoot, readKey: { key }, saveKey: { _ in fatalError("existing draft key replaced") }, syncParent: { fd in
    var checkedPath = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    guard fcntl(fd, F_GETPATH, &checkedPath) == 0 else { return false }
    if String(cString: checkedPath).hasSuffix("/encrypted-vault") { return false }
    return fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
  })
  do { try flushVault.save(saved); fatalError("identical draft skipped chain reflush") } catch {}
  expect(try Data(contentsOf: file) == preserved)
  var installFlushCalls = 0
  let installedVault = ContextPrivateDraftStore(directory: vaultRoot, readKey: { key }, saveKey: { _ in fatalError("installed draft changed key") }, syncParent: { fd in
    var checkedPath = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    guard fcntl(fd, F_GETPATH, &checkedPath) == 0 else { return false }
    if String(cString: checkedPath).hasSuffix("/encrypted-vault") {
      installFlushCalls += 1; if installFlushCalls >= 2 { return false }
    }
    return fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
  })
  do { try installedVault.save(changedDraft); fatalError("installed draft parent failure ignored") }
  catch ContextPrivateDraftStore.Failure.installed {} catch { fatalError("installed draft failure misclassified") }
  expect(try vault.load(scope: owner).text == changedDraft.text)
  do { try installedVault.save(changedDraft); fatalError("installed retry silently succeeded") } catch {}
  try vault.save(saved)
  // Scope cap permits replacement by strict basename, with no cleanup of older scope files.
  var draftQuotaKey: String?
  let draftQuotaRoot = root.appendingPathComponent("draft-quota")
  let quotaDraft = ContextPrivateDraftStore(directory: draftQuotaRoot, readKey: { draftQuotaKey }, saveKey: { draftQuotaKey = $0 })
  try quotaDraft.save(saved)
  let quotaCipher = try Data(contentsOf: draftQuotaRoot.appendingPathComponent(owner + ".v1.aes"))
  for i in 1...63 {
    let other = draftQuotaRoot.appendingPathComponent(String(format: "%064x", i) + ".v1.aes")
    try quotaCipher.write(to: other); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: other.path)
  }
  let newScopeDraft = ContextPrivateSavedDraft(version: 1, scope: String(repeating: "f", count: 64), text: "new scope", references: saved.references, savedAt: stamp)
  do { try quotaDraft.save(newScopeDraft); fatalError("draft scope quota ignored") } catch ContextPrivateDraftStore.Failure.capacity {} catch { fatalError("draft scope capacity misclassified") }
  try quotaDraft.save(changedDraft)
  expect(try quotaDraft.load(scope: owner).text == changedDraft.text)
  var byteDraftKey: String?
  let byteDraftRoot = root.appendingPathComponent("draft-byte-quota")
  let byteDraft = ContextPrivateDraftStore(directory: byteDraftRoot, readKey: { byteDraftKey }, saveKey: { byteDraftKey = $0 })
  try byteDraft.save(saved)
  let byteDraftCipher = try Data(contentsOf: byteDraftRoot.appendingPathComponent(owner + ".v1.aes"))
  for i in 1...32 {
    let other = byteDraftRoot.appendingPathComponent(String(format: "%064x", i) + ".v1.aes")
    expect(FileManager.default.createFile(atPath: other.path, contents: nil, attributes: [.posixPermissions: 0o600]))
    let handle = try FileHandle(forWritingTo: other); try handle.truncate(atOffset: 2_000_000); try handle.close()
  }
  do { try byteDraft.save(changedDraft); fatalError("draft byte quota ignored") } catch ContextPrivateDraftStore.Failure.capacity {} catch { fatalError("draft byte capacity misclassified") }
  expect(try Data(contentsOf: byteDraftRoot.appendingPathComponent(owner + ".v1.aes")) == byteDraftCipher)
  let fullDraftModel = ContextPrivateInbox()
  await fullDraftModel.load(request: { _, _, _ in try page([source]) }, scope: { owner })
  await fullDraftModel.reviewDraft(create: true, request: { _, _, _ in validGate }, scope: { owner })
  fullDraftModel.draft = "new draft blocked by quota"; var draftQuotaReads = 0
  await fullDraftModel.saveDraft(store: byteDraft, request: { _, method, _ in draftQuotaReads += 1; expect(method == "POST"); return validGate }, scope: { owner })
  expect(draftQuotaReads == 1 && fullDraftModel.hasDraft && fullDraftModel.draft == "new draft blocked by quota")
  expect(fullDraftModel.savedStatus.contains("容量已满") || fullDraftModel.savedStatus.contains("capacity reached"))
  expect(try Data(contentsOf: byteDraftRoot.appendingPathComponent(owner + ".v1.aes")) == byteDraftCipher)
  var entriesDraftKey: String?
  let entriesDraftRoot = root.appendingPathComponent("draft-entry-quota")
  let entriesDraft = ContextPrivateDraftStore(directory: entriesDraftRoot, readKey: { entriesDraftKey }, saveKey: { entriesDraftKey = $0 })
  try entriesDraft.save(saved)
  let entryCipher = try Data(contentsOf: entriesDraftRoot.appendingPathComponent(owner + ".v1.aes"))
  for i in 1...255 {
    expect(FileManager.default.createFile(atPath: entriesDraftRoot.appendingPathComponent("foreign-\(i)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  do { try entriesDraft.save(changedDraft); fatalError("draft streamed entry cap ignored") } catch ContextPrivateDraftStore.Failure.capacity {} catch { fatalError("draft entry capacity misclassified") }
  expect(try Data(contentsOf: entriesDraftRoot.appendingPathComponent(owner + ".v1.aes")) == entryCipher)
  expect(try FileManager.default.contentsOfDirectory(atPath: entriesDraftRoot.path).count == 257)
  print("private draft bootstrap retry, static ancestor checks, installed-flush classification and scope/byte quotas passed")
  inbox.reset()
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { owner })
  await inbox.reviewDraft(create: true, request: { _, _, _ in validGate }, scope: { owner })
  inbox.draft = "edited saved draft"
  await inbox.saveDraft(store: vault, request: { _, _, _ in validGate }, scope: { owner })
  expect(try vault.load(scope: owner).text == "edited saved draft")
  let savedSnapshot = try Data(contentsOf: file)
  await inbox.saveDraft(store: vault, request: { _, _, _ in inbox.draft = "changed during verification"; return validGate }, scope: { owner })
  expect(try Data(contentsOf: file) == savedSnapshot)

  await inbox.saveDraft(store: vault, request: { _, _, _ in inbox.suspend(); return validGate }, scope: { owner })
  expect(try Data(contentsOf: file) == savedSnapshot)
  inbox.reset(); inbox.stageSavedDraft(store: vault, scope: { owner })
  inbox.suspend(); expect(!inbox.hasStagedDraft && inbox.draft.isEmpty)
  inbox.stageSavedDraft(store: vault, scope: { owner })
  expect(inbox.hasStagedDraft && !inbox.hasDraft && inbox.draft.isEmpty) // Offline stage shows no text.
  await inbox.revealSavedDraft(request: { _, _, _ in try bytes(fixture["references"]!) }, scope: { owner })
  expect(!inbox.hasDraft && inbox.draft.isEmpty && inbox.hasStagedDraft)
  await inbox.revealSavedDraft(request: { _, _, _ in validGate }, scope: { otherOwner })
  expect(!inbox.hasDraft && inbox.draft.isEmpty)
  await inbox.revealSavedDraft(request: { _, _, _ in validGate }, scope: { owner })
  expect(inbox.draft == "edited saved draft" && inbox.draftValid)
  inbox.reset(); inbox.stageSavedDraft(store: vault, scope: { owner })
  await inbox.revealSavedDraft(request: { _, _, _ in inbox.invalidateDraft(); return validGate }, scope: { owner })
  expect(!inbox.hasDraft && inbox.draft.isEmpty)
  inbox.reset(); inbox.stageSavedDraft(store: vault, scope: { owner })
  await inbox.load(request: { _, _, _ in try page([source]) }, scope: { owner })
  await inbox.reviewDraft(create: true, request: { _, _, _ in validGate }, scope: { owner })
  inbox.draft = "new live draft"
  inbox.stageSavedDraft(store: vault, scope: { owner })
  expect(inbox.draft == "new live draft")
  await inbox.revealSavedDraft(request: { _, _, _ in preconditionFailure("overwriting existing live draft") }, scope: { owner })
  expect(inbox.draft == "new live draft")

  do {
  // Injected request vault only: no actual Keychain, user directories or tenant calls.
  let durableScope = String(repeating: "a", count: 64)
  var requestKey: String?; var requestReads = 0
  let requestRoot = root.appendingPathComponent("request-vault")
  let requestVault = ContextPrivateRequestStore(directory: requestRoot,
    readKey: { requestReads += 1; return requestKey }, saveKey: { requestKey = $0 })
  let beforeInit = requestReads
  let durable = ContextPrivateInbox(requestStore: requestVault)
  expect(requestReads == beforeInit)
  durable.chooseProject("", scope: { durableScope }); durable.setInput("durable original")
  var writes = 0; var pinned: Data?
  await durable.mutate(request: { path, method, body in
    expect(path == "v1/context-private" && method == "POST")
    let onDisk = try requestVault.load(scope: durableScope)
    expect(!onDisk.confirmed && onDisk.pending.data == body && onDisk.scope == durableScope)
    pinned = body; writes += 1
    throw ContextPrivateInbox.Failure.invalid
  }, scope: { durableScope })
  expect(writes == 1 && durable.writeUncertain && pinned != nil)
  let original = try requestVault.load(scope: durableScope)
  let cipherPath = requestRoot.appendingPathComponent(durableScope + ".v1.aes")
  let originalCipher = try Data(contentsOf: cipherPath)
  expect(!String(decoding: originalCipher, as: UTF8.self).contains("durable original"))
  let coldReads = requestReads
  let restarted = ContextPrivateInbox(requestStore: requestVault)
  expect(requestReads == coldReads && restarted.input.isEmpty && !restarted.writeUncertain)
  restarted.stageSavedRequest(scope: { String(repeating: "b", count: 64) })
  expect(!restarted.hasStagedRequest && restarted.input.isEmpty)
  restarted.stageSavedRequest(scope: { durableScope })
  expect(restarted.hasStagedRequest && restarted.input.isEmpty)
  let notObserved = try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "not-observed", "source": NSNull(), "mutationId": NSNull()])
  var reads = 0
  await restarted.restoreSavedRequest(request: { path, method, body in
    reads += 1; expect(method == "GET" && body == nil && path == "v1/context-private-events/" + original.pending.eventId)
    return notObserved
  }, scope: { durableScope })
  expect(reads == 1 && writes == 1 && restarted.writeUncertain && restarted.input.isEmpty)
  await restarted.retryUncertain(request: { path, method, body in
    reads += 1; expect(body == nil && path == "v1/context-private-events/" + original.pending.eventId && method == "GET")
    var ack = source; ack["eventId"] = original.pending.eventId; ack["text"] = "durable original"; ack["tags"] = []
    ack["revision"] = 1; ack["projectId"] = NSNull(); ack["repositoryId"] = NSNull()
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": ack, "mutationId": NSNull()])
  }, scope: { durableScope })
  expect(try reads == 2 && writes == 1 && !restarted.writeUncertain && requestVault.load(scope: durableScope).confirmed)
  let deniedRequestVault = ContextPrivateRequestStore(directory: root.appendingPathComponent("request-denied"),
    readKey: { nil }, saveKey: { _ in throw ContextPrivateRequestStore.Failure.unavailable })
  let deniedRequest = ContextPrivateInbox(requestStore: deniedRequestVault); deniedRequest.chooseProject("", scope: { durableScope }); deniedRequest.setInput("must stay unsent")
  await deniedRequest.mutate(request: { _, _, _ in writes += 1; return Data() }, scope: { durableScope })
  expect(writes == 1 && deniedRequest.input == "must stay unsent" && deniedRequest.failed && !deniedRequest.writeUncertain)
  var finalizeKey: String?; var allowReplace = true
  let finalizeRoot = root.appendingPathComponent("request-finalize")
  let finalizeVault = ContextPrivateRequestStore(directory: finalizeRoot,
    readKey: { finalizeKey }, saveKey: { finalizeKey = $0 }, replace: { from, to in allowReplace && rename(from, to) == 0 })
  let finalize = ContextPrivateInbox(requestStore: finalizeVault); finalize.chooseProject("", scope: { durableScope }); finalize.setInput("ack survived")
  var savedAck: Data?
  await finalize.mutate(request: { _, _, body in
    writes += 1; let payload = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
    var ack = source; ack["eventId"] = payload["eventId"]; ack["text"] = "ack survived"; ack["tags"] = []
    ack["revision"] = 1; ack["projectId"] = NSNull(); ack["repositoryId"] = NSNull()
    savedAck = try bytes(ack); allowReplace = false; return savedAck!
  }, scope: { durableScope })
  expect(try writes == 2 && finalize.writeUncertain && !finalizeVault.load(scope: durableScope).confirmed)
  await finalize.retryUncertain(request: { _, method, _ in
    expect(method == "GET")
    let ack = try JSONSerialization.jsonObject(with: savedAck!)
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": ack, "mutationId": NSNull()])
  }, scope: { durableScope })
  expect(writes == 2 && finalize.writeUncertain) // Known ack but durable finalize failed: zero additional POST.
  allowReplace = true
  await finalize.reconcileUncertain(request: { _, method, _ in
    expect(method == "GET")
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": try JSONSerialization.jsonObject(with: savedAck!), "mutationId": NSNull()])
  }, scope: { durableScope })
  expect(try writes == 2 && !finalize.writeUncertain && finalizeVault.load(scope: durableScope).confirmed)
  let previousKey = requestKey
  requestKey = Data(repeating: 9, count: 32).base64EncodedString()
  expect((try? requestVault.load(scope: durableScope)) == nil)
  expect(try Data(contentsOf: cipherPath) != originalCipher) // Finalized ciphertext differs; wrong key did not mutate it.
  let unchangedCipher = try Data(contentsOf: cipherPath)
  do { try requestVault.save(original); preconditionFailure("wrong key overwritten cipher") } catch {}
  expect(try Data(contentsOf: cipherPath) == unchangedCipher)
  requestKey = previousKey

  let originalRef = ContextPrivateInbox.WithdrawalReference(id: source["id"] as! String,
    eventId: source["eventId"] as! String, revision: 1, createdAt: stamp, changedAt: stamp)
  let editBody = try bytes(["expectedRevision": 1, "text": "durable edit", "tags": [], "requestId": "mutation-edit-1"])
  let editPending = ContextPrivateInbox.PendingWrite(path: "v1/context-private/" + originalRef.id, method: "PUT", data: editBody, mutationId: "mutation-edit-1",
    selected: originalRef, withdrawn: false, eventId: originalRef.eventId, text: "durable edit", tags: [], projectId: nil, repositoryId: nil)
  let editRecord = ContextPrivateSavedRequest(version: 1, scope: durableScope, pending: editPending, confirmed: false)
  try requestVault.save(editRecord)
  let editCipher = try Data(contentsOf: cipherPath)
  do { try requestVault.save(original); preconditionFailure("overwritten immutable unknown request") } catch {}
  expect(try Data(contentsOf: cipherPath) == editCipher)
  let editRestored = ContextPrivateInbox(requestStore: requestVault)
  editRestored.stageSavedRequest(scope: { durableScope })
  var currentScope = durableScope
  var newRevision = source; newRevision["revision"] = 3; newRevision["eventId"] = originalRef.eventId
  await editRestored.restoreSavedRequest(request: { _, method, _ in
    expect(method == "GET"); return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": newRevision, "mutationId": "mutation-edit-1"])
  }, scope: { currentScope })
  expect(editRestored.writeUncertain && editRestored.failed && editRestored.input.isEmpty)
  await editRestored.retryUncertain(request: { _, method, _ in
    expect(method == "GET"); return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": newRevision, "mutationId": "mutation-edit-1"])
  }, scope: { currentScope })
  expect(writes == 2 && editRestored.writeUncertain) // Later revision is not the original ack and cannot be replayed.
  expect(editRestored.requestRecoveryStatus == "invalid-receipt")
  await editRestored.retryUncertain(request: { _, method, body in
    expect(method == "GET" && body == nil)
    var baseline = source; baseline["eventId"] = originalRef.eventId
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": baseline, "mutationId": "mutation-edit-1"])
  }, scope: { currentScope })
  expect(editRestored.writeUncertain && editRestored.failed && editRestored.requestRecoveryStatus == "invalid-receipt" && writes == 2)
  await editRestored.retryUncertain(request: { _, method, body in
    expect(method == "GET" && body == nil)
    throw URLError(.notConnectedToInternet)
  }, scope: { currentScope })
  expect(editRestored.writeUncertain && editRestored.requestRecoveryStatus == "unknown" && writes == 2)

  await editRestored.retryUncertain(request: { _, method, _ in
    expect(method == "GET"); currentScope = String(repeating: "b", count: 64); return notObserved
  }, scope: { currentScope })
  expect(writes == 2 && editRestored.writeUncertain && editRestored.input.isEmpty)
  currentScope = durableScope
  editRestored.suspend()
  expect(editRestored.input.isEmpty && editRestored.writeUncertain && editRestored.canStageSavedRequest)
  editRestored.stageSavedRequest(scope: { durableScope })
  await editRestored.restoreSavedRequest(request: { _, _, _ in return notObserved }, scope: { durableScope })
  var oldRevision = source; oldRevision["revision"] = 1; oldRevision["eventId"] = originalRef.eventId
  oldRevision["projectId"] = NSNull(); oldRevision["repositoryId"] = NSNull()
  await editRestored.retryUncertain(request: { path, method, body in
    expect(method == "GET" && body == nil && path.contains("/requests/"))
    var ack = oldRevision; ack["revision"] = 2; ack["text"] = "durable edit"; ack["tags"] = []
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": ack, "mutationId": "mutation-edit-1"])
  }, scope: { currentScope })
  expect(writes == 2 && !editRestored.writeUncertain)

  // A historical exact nonce proof resolves this edit even if today's source has advanced.
  try requestVault.save(editRecord)
  let proofRestored = ContextPrivateInbox(requestStore: requestVault)
  proofRestored.stageSavedRequest(scope: { durableScope })
  var proof = oldRevision; proof["revision"] = 2; proof["text"] = "durable edit"; proof["tags"] = []
  await proofRestored.restoreSavedRequest(request: { path, method, _ in
    expect(method == "GET" && path.hasSuffix("/requests/mutation-edit-1"))
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": proof, "mutationId": "mutation-edit-1"])
  }, scope: { durableScope })
  expect(!proofRestored.writeUncertain && proofRestored.requestRecoveryStatus == "confirmed" && writes == 2)
  try requestVault.save(editRecord)
  let mismatchProof = ContextPrivateInbox(requestStore: requestVault)
  mismatchProof.stageSavedRequest(scope: { durableScope })
  await mismatchProof.restoreSavedRequest(request: { _, _, _ in
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": proof, "mutationId": "someone-else"])
  }, scope: { durableScope })
  expect(mismatchProof.writeUncertain && mismatchProof.failed && writes == 2)
  let goodCipher = try Data(contentsOf: cipherPath)
  var corruptCipher = goodCipher; corruptCipher[corruptCipher.count-1] ^= 1
  try corruptCipher.write(to: cipherPath)
  expect((try? requestVault.load(scope: durableScope)) == nil)
  do { try requestVault.save(editRecord); preconditionFailure("corrupt cipher overwritten") } catch {}
  expect(try Data(contentsOf: cipherPath) == corruptCipher)
  try goodCipher.write(to: cipherPath) // Restore only this test's own temporary ciphertext.

  var postRenameKey: String?
  let postRenameVault = ContextPrivateRequestStore(directory: root.appendingPathComponent("request-postrename"),
    readKey: { postRenameKey }, saveKey: { postRenameKey = $0 }, syncParent: { fd in
      if FileManager.default.fileExists(atPath: root.appendingPathComponent("request-postrename/" + durableScope + ".v1.aes").path) { return false }
      return fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
    })
  let postRename = ContextPrivateInbox(requestStore: postRenameVault); postRename.chooseProject("", scope: { durableScope }); postRename.setInput("installed but unsent")
  await postRename.mutate(request: { _, _, _ in writes += 1; return Data() }, scope: { durableScope })
  expect(writes == 2 && postRename.writeUncertain && postRename.input == "installed but unsent" && postRename.requestRecoveryStatus == "durability")
  expect(!(try postRenameVault.load(scope: durableScope)).confirmed)

  // Confirmation rename succeeds but directory flush fails: retain durability through GET reconciliation.
  var confirmKey: String?, confirmRenames = 0, confirmWrites = 0
  let confirmVault = ContextPrivateRequestStore(directory: root.appendingPathComponent("confirmation-flush"),
    readKey: { confirmKey }, saveKey: { confirmKey = $0 },
    replace: { from, to in if rename(from, to) != 0 { return false }; confirmRenames += 1; return true },
    syncParent: { fd in confirmRenames < 2 && fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0 })
  let confirmModel = ContextPrivateInbox(requestStore: confirmVault)
  confirmModel.setInput("confirmation flush input", scope: { durableScope })
  await confirmModel.mutate(request: { _, method, _ in
    expect(method == "POST"); confirmWrites += 1; throw URLError(.timedOut)
  }, scope: { durableScope })
  let confirmPending = try confirmVault.load(scope: durableScope).pending
  var installedAck = source; installedAck["eventId"] = confirmPending.eventId
  installedAck["text"] = confirmPending.text; installedAck["tags"] = confirmPending.tags
  installedAck["projectId"] = NSNull(); installedAck["repositoryId"] = NSNull()
  await confirmModel.reconcileUncertain(request: { _, method, body in
    expect(method == "GET" && body == nil)
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": installedAck, "mutationId": NSNull()])
  }, scope: { durableScope })
  expect(confirmWrites == 1 && confirmRenames == 2 && confirmModel.writeUncertain && confirmModel.requestRecoveryStatus == "durability")

  // Non-destructive archive/release and exact-snapshot rollback: isolated temporary keys/files only.
  var archiveKey: String?; let flushOK = true; var failArchiveFlush = false; var failMarkerFlush = false
  let archiveRoot = root.appendingPathComponent("request-archive")
  let archiveVault = ContextPrivateRequestStore(directory: archiveRoot, readKey: { archiveKey }, saveKey: { archiveKey = $0 },
    syncParent: { fd in
      var path = [CChar](repeating: 0, count: Int(MAXPATHLEN))
      guard fcntl(fd, F_GETPATH, &path) == 0 else { return false }
      let name = String(cString: path)
      if !flushOK || (failArchiveFlush && name.hasSuffix("/Archived")) ||
         (failMarkerFlush && name.hasSuffix("/request-archive") && FileManager.default.fileExists(atPath: archiveRoot.appendingPathComponent(durableScope + ".v1.decision").path)) { return false }
      return fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
    })
  try archiveVault.save(original)
  let archiveActive = archiveRoot.appendingPathComponent(durableScope + ".v1.aes")
  let preserved = try Data(contentsOf: archiveActive)
  let review = try archiveVault.reviewArchive(scope: durableScope, pending: original.pending)
  let archiveCopy = archiveRoot.appendingPathComponent("Archived/" + durableScope + "." + review.cipherFingerprint + ".v1.aes")
  let decisionPath = archiveRoot.appendingPathComponent(durableScope + ".v1.decision")
  failArchiveFlush = true
  do { try archiveVault.archive(review, pending: original.pending); fatalError("archive directory flush ignored") } catch {}
  expect(try Data(contentsOf: archiveActive) == preserved)
  expect(!FileManager.default.fileExists(atPath: decisionPath.path))
  failArchiveFlush = false; failMarkerFlush = true
  do { try archiveVault.archive(review, pending: original.pending); fatalError("decision flush ignored") } catch {}
  expect(try Data(contentsOf: archiveCopy) == preserved)
  let replacement = ContextPrivateSavedRequest(version: 1, scope: durableScope, pending: editPending, confirmed: false)
  do { try archiveVault.save(replacement); fatalError("unflushed decision released request") } catch {}
  expect(try Data(contentsOf: archiveActive) == preserved)
  failMarkerFlush = false
  try archiveVault.archive(review, pending: original.pending)
  try archiveVault.archive(review, pending: original.pending)
  expect(try FileManager.default.contentsOfDirectory(atPath: archiveRoot.appendingPathComponent("Archived").path).count == 1)
  let releasedBytes = try Data(contentsOf: archiveActive)
  try archiveVault.save(original)
  expect(try Data(contentsOf: archiveActive) == releasedBytes)
  var corruptedArchive = preserved; corruptedArchive[corruptedArchive.count - 1] ^= 1
  try corruptedArchive.write(to: archiveCopy)
  do { try archiveVault.save(replacement); fatalError("corrupt copy released original") } catch {}
  expect(try Data(contentsOf: archiveActive) == preserved)
  try preserved.write(to: archiveCopy)
  let malformedReview = ContextPrivateRequestStore.ArchiveReview(scope: durableScope, cipherFingerprint: review.cipherFingerprint, eventFingerprint: "malformed")
  do { try archiveVault.holdArchived(malformedReview); fatalError("malformed review wrote decision") } catch {}
  try archiveVault.holdArchived(review)
  do { try archiveVault.save(replacement); fatalError("held request replaced") } catch {}
  try archiveVault.archive(review, pending: original.pending)
  try archiveVault.save(replacement)
  expect(try Data(contentsOf: archiveCopy) == preserved)
  do { try archiveVault.holdArchived(review); fatalError("rollback overwrote newer request") } catch {}
  do { try archiveVault.archive(review, pending: editPending); fatalError("stale cipher release") } catch {}
  do { try archiveVault.save(original); fatalError("old marker released new unknown") } catch {}
  let wrongArchiveKey = ContextPrivateRequestStore(directory: archiveRoot, readKey: { Data(repeating: 3, count: 32).base64EncodedString() }, saveKey: { _ in fatalError("archive regenerated key") })
  do { _ = try wrongArchiveKey.reviewArchive(scope: durableScope, pending: editPending); fatalError("wrong key archive") } catch {}
  let liveArchive = try archiveVault.load(scope: durableScope)
  let currentReview = try archiveVault.reviewArchive(scope: durableScope, pending: liveArchive.pending)
  // Capacity is a refusal, never a cleanup of older archives.
  let archiveDir = archiveRoot.appendingPathComponent("Archived")
  for i in 1..<64 { try preserved.write(to: archiveDir.appendingPathComponent(durableScope + "." + String(format: "%064x", i) + ".v1.aes")); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: archiveDir.appendingPathComponent(durableScope + "." + String(format: "%064x", i) + ".v1.aes").path) }
  do { try archiveVault.archive(currentReview, pending: liveArchive.pending); fatalError("archive count bound ignored") } catch {}
  expect(try archiveVault.load(scope: durableScope).pending == liveArchive.pending)
  let archiveModel = ContextPrivateInbox(requestStore: archiveVault)
  archiveModel.stageSavedRequest(scope: { durableScope })
  archiveModel.reviewArchive(scope: { durableScope })
  expect(archiveModel.archiveReview != nil)
  archiveModel.confirmArchive(scope: { String(repeating: "e", count: 64) })
  expect(archiveModel.requestRecoveryStatus == "archive-failed" && archiveModel.input.isEmpty)
  let response = ContextPrivateHTTPFailure(status: 413, data: try bytes(["detail": "PRIVATE_CONTEXT_CAPACITY"]))
  expect(response.status == 413 && response.code == "PRIVATE_CONTEXT_CAPACITY")
  expect(ContextPrivateHTTPFailure(status: 502, data: try bytes(["detail": "secret unexpected text"])).code == nil)
  expect(ContextPrivateHTTPFailure(status: 422, data: try bytes(["detail": ["text": "not retained"]])).code == nil)

  var modelArchiveKey: String?
  let modelArchiveVault = ContextPrivateRequestStore(directory: root.appendingPathComponent("request-model-archive"), readKey: { modelArchiveKey }, saveKey: { modelArchiveKey = $0 })
  try modelArchiveVault.save(original)
  let archiveSuccess = ContextPrivateInbox(requestStore: modelArchiveVault)
  archiveSuccess.stageSavedRequest(scope: { durableScope })
  archiveSuccess.reviewArchive(scope: { durableScope })
  expect(archiveSuccess.archiveReview != nil)
  archiveSuccess.confirmArchive(scope: { durableScope })
  expect(archiveSuccess.requestRecoveryStatus == "archived" && archiveSuccess.archiveUndo != nil && archiveSuccess.input.isEmpty && !archiveSuccess.writeUncertain)
  archiveSuccess.undoArchive(scope: { durableScope })
  expect(archiveSuccess.requestRecoveryStatus == "archive-held" && archiveSuccess.archiveUndo == nil)
  do { try modelArchiveVault.save(replacement); fatalError("model rollback failed to hold") } catch {}
  archiveSuccess.stageSavedRequest(scope: { durableScope })
  archiveSuccess.reviewArchive(scope: { durableScope }); archiveSuccess.suspend()
  archiveSuccess.confirmArchive(scope: { durableScope })
  expect(archiveSuccess.archiveReview == nil && archiveSuccess.archiveUndo == nil)


  let junkDir = root.appendingPathComponent("request-model-archive/Archived")
  try Data().write(to: junkDir.appendingPathComponent(".request-crash-leftover"))
  try Data("foreign".utf8).write(to: junkDir.appendingPathComponent(".DS_Store"))
  try modelArchiveVault.save(original) // Held same unknown must preserve the active bytes.
  let junkReview = try modelArchiveVault.reviewArchive(scope: durableScope, pending: original.pending)
  try modelArchiveVault.archive(junkReview, pending: original.pending)
  expect(FileManager.default.fileExists(atPath: junkDir.appendingPathComponent(".DS_Store").path))
  try modelArchiveVault.holdArchived(junkReview)
  let switchingArchive = ContextPrivateInbox(requestStore: modelArchiveVault)
  var scopeReads = 0
  func changingArchiveScope() throws -> String { scopeReads += 1; return scopeReads < 4 ? durableScope : String(repeating: "d", count: 64) }
  switchingArchive.stageSavedRequest(scope: changingArchiveScope) // read1
  switchingArchive.reviewArchive(scope: changingArchiveScope) // read2
  switchingArchive.confirmArchive(scope: changingArchiveScope) // read3 before release, read4 after release
  expect(switchingArchive.archiveReview == nil && switchingArchive.archiveUndo == nil && switchingArchive.requestRecoveryStatus.isEmpty)
  try modelArchiveVault.save(replacement) // Original-scope durable release did succeed, despite hidden new-scope UI.
  var errorStatus = 413, errorData = try bytes(["detail": "PRIVATE_CONTEXT_CAPACITY"]), errorCalls = 0, flipErrorActor = false
  var errorCredential = try InputBackupClient.bindCredential("actor-a", to: config.endpoint)
  let errors = InputBackupClient(directory: root.appendingPathComponent("private-http-errors"), credential: { errorCredential }, send: { req in
    errorCalls += 1
    expect(req.url?.path == "/v1/context-private" && req.httpMethod == "POST")
    if flipErrorActor { errorCredential = try InputBackupClient.bindCredential("actor-b", to: config.endpoint) }
    return (errorData, errorStatus)
  })
  try errors.configure(config)
  for code in [400, 413, 409, 502, 422] {
    errorStatus = code; errorData = code == 422 ? Data("malformed raw body not retained".utf8) : try bytes(["detail": "PRIVATE_CONTEXT_CAPACITY"])
    do { _ = try await errors.privateContextRequest(path: "v1/context-private", method: "POST", body: original.pending.data); fatalError("non-200 private reply accepted") }
    catch let reply as ContextPrivateHTTPFailure { expect(reply.status == code && (code != 422 || reply.code == nil)) }
  }
  errorStatus = 413; errorData = try bytes(["detail": "PRIVATE_CONTEXT_CAPACITY"])
  var replyKey: String?
  let replyVault = ContextPrivateRequestStore(directory: root.appendingPathComponent("private-rejected-original"), readKey: { replyKey }, saveKey: { replyKey = $0 })
  let repliedModel = ContextPrivateInbox(requestStore: replyVault); repliedModel.chooseProject("", scope: { durableScope }); repliedModel.setInput("retained original after server rejection")
  await repliedModel.mutate(request: { path, method, body in try await errors.privateContextRequest(path: path, method: method, body: body) }, scope: { durableScope })
  expect(repliedModel.writeUncertain && repliedModel.serverReplyStatus == 413 && repliedModel.serverReplyCode == "PRIVATE_CONTEXT_CAPACITY")
  expect(!(try replyVault.load(scope: durableScope)).confirmed && errors.pendingCount == 0)
  flipErrorActor = true
  do { _ = try await errors.privateContextRequest(path: "v1/context-private", method: "POST", body: original.pending.data); fatalError("stale actor response exposed") }
  catch { expect(!(error is ContextPrivateHTTPFailure)) }
  expect(errorCalls == 7 && errors.pendingCount == 0)


  var bootstrapKeyWrites = 0, bootstrapHTTP = 0
  let bootstrapRoot = root.appendingPathComponent("request-bootstrap/Requests")
  let bootstrap = ContextPrivateRequestStore(directory: bootstrapRoot, readKey: { nil }, saveKey: { _ in bootstrapKeyWrites += 1 }, syncParent: { fd in
    var path = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    guard fcntl(fd, F_GETPATH, &path) == 0 else { return false }
    if String(cString: path).hasSuffix("/queue") { return false } // Newly created first child parent entry flush.
    return fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
  })
  let bootstrapModel = ContextPrivateInbox(requestStore: bootstrap); bootstrapModel.chooseProject("", scope: { durableScope }); bootstrapModel.setInput("bootstrap unsent")
  await bootstrapModel.mutate(request: { _, _, _ in bootstrapHTTP += 1; return Data() }, scope: { durableScope })
  expect(bootstrapHTTP == 0 && bootstrapKeyWrites == 0 && bootstrapModel.input == "bootstrap unsent")
  expect(!FileManager.default.fileExists(atPath: bootstrapRoot.appendingPathComponent(durableScope + ".v1.aes").path))
  await bootstrapModel.mutate(request: { _, _, _ in bootstrapHTTP += 1; return Data() }, scope: { durableScope })
  expect(bootstrapHTTP == 0 && bootstrapKeyWrites == 0)
  let coldBootstrap = ContextPrivateRequestStore(directory: bootstrapRoot, readKey: { nil }, saveKey: { _ in bootstrapKeyWrites += 1 }, syncParent: { fd in
    var path = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    guard fcntl(fd, F_GETPATH, &path) == 0 else { return false }
    return !String(cString: path).hasSuffix("/queue") && fsync(fd) == 0 && fcntl(fd, F_FULLFSYNC) == 0
  })
  do { try coldBootstrap.save(original); fatalError("visible creation chain bypassed durability") } catch {}
  expect(bootstrapHTTP == 0 && bootstrapKeyWrites == 0)



  var byteQuotaKey: String?
  let byteQuotaRoot = root.appendingPathComponent("request-byte-quota")
  let byteQuotaVault = ContextPrivateRequestStore(directory: byteQuotaRoot, readKey: { byteQuotaKey }, saveKey: { byteQuotaKey = $0 })
  try byteQuotaVault.save(original)
  let byteReview = try byteQuotaVault.reviewArchive(scope: durableScope, pending: original.pending)
  let quotaDir = byteQuotaRoot.appendingPathComponent("Archived")
  try FileManager.default.createDirectory(at: quotaDir, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  for i in 1...32 {
    let path = quotaDir.appendingPathComponent(durableScope + "." + String(format: "%064x", i) + ".v1.aes")
    expect(FileManager.default.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600]))
    let file = try FileHandle(forWritingTo: path); try file.truncate(atOffset: 2_000_000); try file.close()
  }
  do { try byteQuotaVault.archive(byteReview, pending: original.pending); fatalError("archive byte bound ignored") } catch {}
  expect(try byteQuotaVault.load(scope: durableScope).pending == original.pending)
  expect(!FileManager.default.fileExists(atPath: byteQuotaRoot.appendingPathComponent(durableScope + ".v1.decision").path))


  var inputKey: String?; var inputKeyReads = 0
  let inputRoot = root.appendingPathComponent("private-unsubmitted-input")
  let inputVault = ContextPrivateInputStore(directory: inputRoot, readKey: { inputKeyReads += 1; return inputKey }, saveKey: { inputKey = $0 })
  expect(inputKeyReads == 0 && !FileManager.default.fileExists(atPath: inputRoot.path))
  let savedInput = ContextPrivateSavedInput(version: 1, scope: durableScope, text: "offline unsent editor", tag: "question", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp)
  try inputVault.save(savedInput)
  let inputPath = inputRoot.appendingPathComponent(durableScope + ".v1.aes")
  let inputCipher = try Data(contentsOf: inputPath)
  expect(!String(decoding: inputCipher, as: UTF8.self).contains("offline unsent editor"))
  let coldInput = ContextPrivateInputStore(directory: inputRoot, readKey: { inputKey }, saveKey: { _ in fatalError("cold input key overwrite") })
  expect(try coldInput.load(scope: durableScope).text == savedInput.text)
  let editedInput = ContextPrivateSavedInput(version: 1, scope: durableScope, text: "offline edited source", tag: "progress", reference: editPending.selected!, projectId: nil, repositoryId: nil, savedAt: stamp)
  try inputVault.save(editedInput)
  expect(try inputVault.load(scope: durableScope).reference == editPending.selected!)
  let changedInputCipher = try Data(contentsOf: inputPath)
  let wrongInputKey = ContextPrivateInputStore(directory: inputRoot, readKey: { Data(repeating: 6, count: 32).base64EncodedString() }, saveKey: { _ in fatalError("wrong input key regenerated") })
  do { try wrongInputKey.save(savedInput); fatalError("wrong input key overwrote original") } catch {}
  expect(try Data(contentsOf: inputPath) == changedInputCipher)
  let missingInputKey = ContextPrivateInputStore(directory: inputRoot, readKey: { nil }, saveKey: { _ in fatalError("missing input key regenerated") })
  do { _ = try missingInputKey.load(scope: durableScope); fatalError("missing input key") } catch {}
  do { try missingInputKey.save(savedInput); fatalError("missing input key replaced original") } catch {}
  let foreignInputPath = inputRoot.appendingPathComponent(String(repeating: "e", count: 64) + ".v1.aes")
  try changedInputCipher.write(to: foreignInputPath); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: foreignInputPath.path)
  do { _ = try inputVault.load(scope: String(repeating: "e", count: 64)); fatalError("foreign input scope revealed") } catch {}
  var corruptInputCipher = changedInputCipher; corruptInputCipher[corruptInputCipher.count - 1] ^= 1
  try corruptInputCipher.write(to: inputPath)
  do { try inputVault.save(savedInput); fatalError("corrupt input replaced") } catch {}
  expect(try Data(contentsOf: inputPath) == corruptInputCipher)
  try changedInputCipher.write(to: inputPath)
  expect(!ContextPrivateSavedInput(version: 2, scope: durableScope, text: "invalid", tag: "", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp).valid)
  expect(!ContextPrivateSavedInput(version: 1, scope: durableScope, text: String(repeating: "x", count: 8193), tag: "", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp).valid)
  expect(!ContextPrivateSavedInput(version: 1, scope: durableScope, text: "invalid", tag: "unexpected", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp).valid)

  for i in 1...62 {
    let path = inputRoot.appendingPathComponent(String(format: "%064x", i) + ".v1.aes")
    expect(FileManager.default.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600]))
    let file = try FileHandle(forWritingTo: path); try file.truncate(atOffset: 64_000); try file.close()
  }
  let extraInput = ContextPrivateSavedInput(version: 1, scope: String(repeating: "f", count: 64), text: "new scope refused", tag: "", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp)
  do { try inputVault.save(extraInput); fatalError("input scope-file quota ignored") } catch {}
  let largeInput = ContextPrivateSavedInput(version: 1, scope: durableScope, text: String(repeating: "\u{0001}", count: 8192), tag: "", reference: nil, projectId: nil, repositoryId: nil, savedAt: stamp)
  expect(largeInput.valid)
  do { try inputVault.save(largeInput); fatalError("input byte quota ignored") } catch {}
  expect(try Data(contentsOf: inputPath) == changedInputCipher)


  // Streamed enumeration boundaries: exact cap supports existing replacement; overflow refuses without cleanup.
  var scanInputKey: String?
  let scanInputRoot = root.appendingPathComponent("input-stream-boundary")
  let scanInput = ContextPrivateInputStore(directory: scanInputRoot, readKey: { scanInputKey }, saveKey: { scanInputKey = $0 })
  try scanInput.save(savedInput)
  for index in 0..<254 {
    expect(FileManager.default.createFile(atPath: scanInputRoot.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  try scanInput.save(editedInput)
  expect(try scanInput.load(scope: durableScope) == editedInput)
  let scanInputPath = scanInputRoot.appendingPathComponent(durableScope + ".v1.aes")
  let scanCipher = try Data(contentsOf: scanInputPath)
  expect(FileManager.default.createFile(atPath: scanInputRoot.appendingPathComponent("foreign-overflow").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  do { try scanInput.save(savedInput); fatalError("input streamed cap ignored") } catch ContextPrivateInputStore.Failure.capacity {} catch { fatalError("wrong input scan failure") }
  expect(try Data(contentsOf: scanInputPath) == scanCipher)
  expect(try scanInput.load(scope: durableScope) == editedInput) // Reads remain possible above cap.
  let scanModelRequests = ContextPrivateRequestStore(directory: root.appendingPathComponent("scan-model-requests"), readKey: { nil }, saveKey: { _ in fatalError("offline save created request key") })
  let scanModel = ContextPrivateInbox(requestStore: scanModelRequests, inputStore: scanInput)
  scanModel.chooseProject("", scope: { durableScope }); scanModel.setInput("visible editor must survive scan refusal")
  scanModel.saveInputOffline(scope: { durableScope })
  expect(scanModel.input == "visible editor must survive scan refusal" && scanModel.inputSavedStatus == "capacity")
  expect(try Data(contentsOf: scanInputPath) == scanCipher)
  expect(try FileManager.default.contentsOfDirectory(atPath: scanInputRoot.path).count == 257)

  // Active request-root allocation budgets: existing reads/idempotent unknowns survive; no new dispatch on refusal.
  var rootQuotaKey: String?
  let rootQuotaDir = root.appendingPathComponent("request-active-scope-quota")
  let rootQuota = ContextPrivateRequestStore(directory: rootQuotaDir, readKey: { rootQuotaKey }, saveKey: { rootQuotaKey = $0 })
  try rootQuota.save(original)
  let rootQuotaFile = rootQuotaDir.appendingPathComponent(durableScope + ".v1.aes")
  let rootQuotaCipher = try Data(contentsOf: rootQuotaFile)
  for index in 1..<63 {
    let path = rootQuotaDir.appendingPathComponent(String(format: "%064x", index) + ".v1.aes")
    try rootQuotaCipher.write(to: path); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
  }
  let extraScope = String(repeating: "b", count: 64)
  let extraRequest = ContextPrivateSavedRequest(version: 1, scope: extraScope, pending: original.pending, confirmed: false)
  try rootQuota.save(extraRequest) // New scope accepted at63 snapshots, now exactly64.
  expect(try rootQuota.load(scope: extraScope).pending == original.pending)
  let deniedScope = String(repeating: "c", count: 64)
  let deniedScopeRequest = ContextPrivateSavedRequest(version: 1, scope: deniedScope, pending: original.pending, confirmed: false)
  do { try rootQuota.save(deniedScopeRequest); fatalError("active request scope quota ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong request scope failure") }
  try rootQuota.save(original) // No new cipher/slot: same unknown pending is idempotent.
  expect(try Data(contentsOf: rootQuotaFile) == rootQuotaCipher)
  try rootQuota.save(.init(version: 1, scope: durableScope, pending: original.pending, confirmed: true))
  expect(try rootQuota.load(scope: durableScope).confirmed) // Existing scope confirmation works at64 cap.
  let rootQuotaConfirmed = try Data(contentsOf: rootQuotaFile)
  let quotaRequestModel = ContextPrivateInbox(requestStore: rootQuota)
  quotaRequestModel.chooseProject("", scope: { deniedScope }); quotaRequestModel.setInput("retain editor at active request cap")
  var rootQuotaHTTP = 0
  await quotaRequestModel.mutate(request: { _, _, _ in rootQuotaHTTP += 1; return Data() }, scope: { deniedScope })
  expect(rootQuotaHTTP == 0 && !quotaRequestModel.writeUncertain && quotaRequestModel.requestRecoveryStatus == "capacity")
  expect(quotaRequestModel.input == "retain editor at active request cap")
  expect(try Data(contentsOf: rootQuotaFile) == rootQuotaConfirmed)

  var rootBytesKey: String?
  let rootBytesDir = root.appendingPathComponent("request-active-byte-quota")
  let rootBytes = ContextPrivateRequestStore(directory: rootBytesDir, readKey: { rootBytesKey }, saveKey: { rootBytesKey = $0 })
  try rootBytes.save(original)
  for index in 1...32 {
    let suffix = index == 32 ? ".v1.decision" : ".v1.aes" // Decision bytes count in root budget too.
    let path = rootBytesDir.appendingPathComponent(String(format: "%064x", index) + suffix)
    expect(FileManager.default.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600]))
    let handle = try FileHandle(forWritingTo: path); try handle.truncate(atOffset: 2_000_000); try handle.close()
  }
  let rootBytesFile = rootBytesDir.appendingPathComponent(durableScope + ".v1.aes")
  let rootBytesOriginal = try Data(contentsOf: rootBytesFile)
  do { try rootBytes.save(.init(version: 1, scope: durableScope, pending: original.pending, confirmed: true)); fatalError("root active/decision byte quota ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong request byte failure") }
  expect(try Data(contentsOf: rootBytesFile) == rootBytesOriginal && !(try rootBytes.load(scope: durableScope)).confirmed)
  try rootBytes.save(original) // Above-byte-cap exact unknown remains durable/readable, not auto acknowledged.
  expect(try Data(contentsOf: rootBytesFile) == rootBytesOriginal)

  var rootEntryKey: String?
  let rootEntryDir = root.appendingPathComponent("request-active-entry-quota")
  let rootEntries = ContextPrivateRequestStore(directory: rootEntryDir, readKey: { rootEntryKey }, saveKey: { rootEntryKey = $0 })
  try rootEntries.save(original)
  for index in 0..<253 {
    expect(FileManager.default.createFile(atPath: rootEntryDir.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  try rootEntries.save(extraRequest) // New scope at255 entries succeeds; next allocation would exceed256.
  expect(try rootEntries.load(scope: extraScope).pending == original.pending)
  do { try rootEntries.save(deniedScopeRequest); fatalError("root exact entry quota allocated new scope") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong exact request entry failure") }
  try rootEntries.save(.init(version: 1, scope: durableScope, pending: original.pending, confirmed: true))
  expect(try rootEntries.load(scope: durableScope).confirmed)
  expect(FileManager.default.createFile(atPath: rootEntryDir.appendingPathComponent("overflow").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  do { try rootEntries.save(.init(version: 1, scope: durableScope, pending: original.pending, confirmed: true)); fatalError("root streamed overflow ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong root overflow failure") }
  expect(try rootEntries.load(scope: durableScope).confirmed && (try FileManager.default.contentsOfDirectory(atPath: rootEntryDir.path)).count == 257)

  // Archive copy can succeed while root decision allocation refuses; no release of the unknown original.
  var markerQuotaKey: String?
  let markerQuotaDir = root.appendingPathComponent("request-decision-entry-quota")
  let markerQuota = ContextPrivateRequestStore(directory: markerQuotaDir, readKey: { markerQuotaKey }, saveKey: { markerQuotaKey = $0 })
  try markerQuota.save(original)
  let markerReview = try markerQuota.reviewArchive(scope: durableScope, pending: original.pending)
  let markerArchiveDir = markerQuotaDir.appendingPathComponent("Archived")
  try FileManager.default.createDirectory(at: markerArchiveDir, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  for index in 0..<253 {
    expect(FileManager.default.createFile(atPath: markerQuotaDir.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  do { try markerQuota.archive(markerReview, pending: original.pending); fatalError("decision allocation exceeded root entry cap") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong marker capacity failure") }
  expect(try FileManager.default.contentsOfDirectory(atPath: markerArchiveDir.path).count == 1)
  expect(!FileManager.default.fileExists(atPath: markerQuotaDir.appendingPathComponent(durableScope + ".v1.decision").path))
  expect(try markerQuota.load(scope: durableScope).pending == original.pending)
  let markerActiveBytes = try Data(contentsOf: markerQuotaDir.appendingPathComponent(durableScope + ".v1.aes"))
  do { try markerQuota.save(replacement); fatalError("unconfirmed archive copy released original without decision") } catch ContextPrivateRequestStore.Failure.unavailable {} catch { fatalError("wrong unreleased request failure") }
  expect(try Data(contentsOf: markerQuotaDir.appendingPathComponent(durableScope + ".v1.aes")) == markerActiveBytes)

  var exactBytesKey: String?
  let exactBytesDir = root.appendingPathComponent("request-exact-byte-boundary")
  let exactBytes = ContextPrivateRequestStore(directory: exactBytesDir, readKey: { exactBytesKey }, saveKey: { exactBytesKey = $0 })
  try exactBytes.save(original)
  let exactOriginalSize = try Data(contentsOf: exactBytesDir.appendingPathComponent(durableScope + ".v1.aes")).count
  let extraSealedSize = try JSONEncoder().encode(extraRequest).count + 28
  for index in 1...32 {
    let path = exactBytesDir.appendingPathComponent(String(format: "%064x", index) + ".v1.aes")
    expect(FileManager.default.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600]))
    let size = index == 32 ? 64_000_000 - exactOriginalSize - extraSealedSize - 31 * 2_000_000 : 2_000_000
    let handle = try FileHandle(forWritingTo: path); try handle.truncate(atOffset: UInt64(size)); try handle.close()
  }
  try exactBytes.save(extraRequest) // Exact64MB must succeed (<=, not <).
  expect(try exactBytes.load(scope: extraScope).pending == original.pending)
  do { try exactBytes.save(deniedScopeRequest); fatalError("extra allocation beyond exact64MB") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong exact byte capacity failure") }

  // Descriptor-based quota metadata must reject unsafe recognized siblings before any model dispatch.
  for kind in ["symlink", "hardlink", "public-mode", "oversize", "directory"] {
    var unsafeKey: String?
    let unsafeDir = root.appendingPathComponent("request-unsafe-sibling-" + kind)
    let unsafeVault = ContextPrivateRequestStore(directory: unsafeDir, readKey: { unsafeKey }, saveKey: { unsafeKey = $0 })
    try unsafeVault.save(original)
    let originalPath = unsafeDir.appendingPathComponent(durableScope + ".v1.aes")
    let originalBytes = try Data(contentsOf: originalPath)
    let sibling = unsafeDir.appendingPathComponent(String(repeating: "f", count: 64) + ".v1.aes")
    switch kind {
    case "symlink": try FileManager.default.createSymbolicLink(at: sibling, withDestinationURL: originalPath)
    case "hardlink": expect(link(originalPath.path, sibling.path) == 0)
    case "directory": try FileManager.default.createDirectory(at: sibling, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    default:
      expect(FileManager.default.createFile(atPath: sibling.path, contents: Data([1]), attributes: [.posixPermissions: kind == "public-mode" ? 0o644 : 0o600]))
      if kind == "oversize" { let handle = try FileHandle(forWritingTo: sibling); try handle.truncate(atOffset: 2_000_001); try handle.close() }
    }
    do { try unsafeVault.save(extraRequest); fatalError("unsafe recognized sibling allocated request") } catch ContextPrivateRequestStore.Failure.unavailable {} catch { fatalError("unsafe sibling incorrectly classified") }
    let unsafeModel = ContextPrivateInbox(requestStore: unsafeVault); unsafeModel.chooseProject("", scope: { durableScope }); unsafeModel.setInput("unsafe sibling retains editor")
    var unsafeHTTP = 0
    await unsafeModel.mutate(request: { _, _, _ in unsafeHTTP += 1; return Data() }, scope: { extraScope })
    expect(unsafeHTTP == 0 && unsafeModel.input == "unsafe sibling retains editor" && unsafeModel.failed && !unsafeModel.writeUncertain)
    expect(try Data(contentsOf: originalPath) == originalBytes)
  }

  // A valid synthetic acknowledgment cannot become confirmed when local quota blocks its confirmation write.
  var ackQuotaKey: String?
  let ackQuotaDir = root.appendingPathComponent("request-ack-quota")
  let ackQuota = ContextPrivateRequestStore(directory: ackQuotaDir, readKey: { ackQuotaKey }, saveKey: { ackQuotaKey = $0 })
  let ackQuotaModel = ContextPrivateInbox(requestStore: ackQuota); ackQuotaModel.chooseProject("", scope: { durableScope }); ackQuotaModel.setInput("ack must remain unknown on capacity failure")
  var quotaAck: Data?; var ackQuotaHTTP = 0; var ackQuotaCipher: Data?
  await ackQuotaModel.mutate(request: { _, _, body in
    ackQuotaHTTP += 1
    let payload = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
    ackQuotaCipher = try Data(contentsOf: ackQuotaDir.appendingPathComponent(durableScope + ".v1.aes"))
    for index in 1...32 {
      let path = ackQuotaDir.appendingPathComponent(String(format: "%064x", index) + ".v1.aes")
      expect(FileManager.default.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600]))
      let handle = try FileHandle(forWritingTo: path); try handle.truncate(atOffset: 2_000_000); try handle.close()
    }
    var ack = source; ack["eventId"] = payload["eventId"]; ack["text"] = "ack must remain unknown on capacity failure"; ack["tags"] = []
    ack["revision"] = 1; ack["projectId"] = NSNull(); ack["repositoryId"] = NSNull()
    quotaAck = try bytes(ack); return quotaAck!
  }, scope: { durableScope })
  expect(ackQuotaHTTP == 1 && ackQuotaModel.writeUncertain && ackQuotaModel.input == "ack must remain unknown on capacity failure")
  expect(try !ackQuota.load(scope: durableScope).confirmed && Data(contentsOf: ackQuotaDir.appendingPathComponent(durableScope + ".v1.aes")) == ackQuotaCipher!)
  await ackQuotaModel.reconcileUncertain(request: { _, method, _ in
    expect(method == "GET")
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "observed", "source": try JSONSerialization.jsonObject(with: quotaAck!), "mutationId": NSNull()])
  }, scope: { durableScope })
  expect(try ackQuotaHTTP == 1 && ackQuotaModel.writeUncertain && !ackQuota.load(scope: durableScope).confirmed)

  // A missing key plus an oversized populated request directory cannot generate a replacement key or dispatch HTTP.
  let scanRequestRoot = root.appendingPathComponent("request-stream-key")
  try FileManager.default.createDirectory(at: scanRequestRoot, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  for index in 0..<256 {
    expect(FileManager.default.createFile(atPath: scanRequestRoot.appendingPathComponent("orphan-\(index)").path, contents: Data([1]), attributes: [.posixPermissions: 0o600]))
  }
  var scanKeyWrites = 0
  let scanRequests = ContextPrivateRequestStore(directory: scanRequestRoot, readKey: { nil }, saveKey: { _ in scanKeyWrites += 1 })
  do { try scanRequests.save(original); fatalError("request missing-key scan cap ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong request scan failure") }
  let scanRequestModel = ContextPrivateInbox(requestStore: scanRequests)
  scanRequestModel.chooseProject("", scope: { durableScope }); scanRequestModel.setInput("no dispatch after directory refusal")
  var scanHTTP = 0
  await scanRequestModel.mutate(request: { _, _, _ in scanHTTP += 1; return Data() }, scope: { durableScope })
  expect(scanHTTP == 0 && scanKeyWrites == 0 && scanRequestModel.input == "no dispatch after directory refusal")
  expect(try FileManager.default.contentsOfDirectory(atPath: scanRequestRoot.path).count == 257)

  var exactArchiveKey: String?
  let exactArchiveRoot = root.appendingPathComponent("archive-exact-boundary")
  let exactArchive = ContextPrivateRequestStore(directory: exactArchiveRoot, readKey: { exactArchiveKey }, saveKey: { exactArchiveKey = $0 })
  try exactArchive.save(original)
  let exactReview = try exactArchive.reviewArchive(scope: durableScope, pending: original.pending)
  try exactArchive.archive(exactReview, pending: original.pending) // Production creates the private directory.
  let exactArchiveDir = exactArchiveRoot.appendingPathComponent("Archived")
  var exactMode = stat(); expect(lstat(exactArchiveDir.path, &exactMode) == 0 && exactMode.st_mode & 0o077 == 0)
  try exactArchive.holdArchived(exactReview)
  try exactArchive.archive(exactReview, pending: original.pending)
  try exactArchive.save(replacement)
  let nextExactReview = try exactArchive.reviewArchive(scope: durableScope, pending: replacement.pending)
  for index in 0..<255 { // One existing ciphertext + 255 foreign entries = exact scan cap.
    expect(FileManager.default.createFile(atPath: exactArchiveDir.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  let exactActive = try Data(contentsOf: exactArchiveRoot.appendingPathComponent(durableScope + ".v1.aes"))
  do { try exactArchive.archive(nextExactReview, pending: replacement.pending); fatalError("archive exact cap allocated 257th entry") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong exact archive capacity error") }
  expect(try Data(contentsOf: exactArchiveRoot.appendingPathComponent(durableScope + ".v1.aes")) == exactActive)
  // Remove only this fixture's empty sentinel to test one available slot, never production data.
  try FileManager.default.removeItem(at: exactArchiveDir.appendingPathComponent("foreign-254"))
  try exactArchive.archive(nextExactReview, pending: replacement.pending)
  expect(try FileManager.default.contentsOfDirectory(atPath: exactArchiveDir.path).count == 256)
  try exactArchive.archive(nextExactReview, pending: replacement.pending) // Idempotent recovery at exact cap.
  expect(try exactArchive.load(scope: durableScope).pending == replacement.pending)

  var scanArchiveKey: String?
  let scanArchiveRoot = root.appendingPathComponent("archive-stream-boundary")
  let scanArchive = ContextPrivateRequestStore(directory: scanArchiveRoot, readKey: { scanArchiveKey }, saveKey: { scanArchiveKey = $0 })
  try scanArchive.save(original)
  let scanArchiveReview = try scanArchive.reviewArchive(scope: durableScope, pending: original.pending)
  let scanArchiveDir = scanArchiveRoot.appendingPathComponent("Archived")
  try FileManager.default.createDirectory(at: scanArchiveDir, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  for index in 0..<257 {
    expect(FileManager.default.createFile(atPath: scanArchiveDir.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
  }
  let scanArchiveActive = scanArchiveRoot.appendingPathComponent(durableScope + ".v1.aes")
  let scanArchiveCipher = try Data(contentsOf: scanArchiveActive)
  do { try scanArchive.archive(scanArchiveReview, pending: original.pending); fatalError("archive streamed cap ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { fatalError("wrong archive scan failure") }
  expect(try Data(contentsOf: scanArchiveActive) == scanArchiveCipher)
  expect(!FileManager.default.fileExists(atPath: scanArchiveRoot.appendingPathComponent(durableScope + ".v1.decision").path))
  expect(try FileManager.default.contentsOfDirectory(atPath: scanArchiveDir.path).count == 257)

  // After rename, the former temp pathname no longer belongs to this write and must not be unlinked.
  var installInputKey: String?; var recreatedInputTemp: String?
  let installedInput = ContextPrivateInputStore(directory: root.appendingPathComponent("input-temp-install"), readKey: { installInputKey }, saveKey: { installInputKey = $0 }, replace: { from, to in
    guard rename(from, to) == 0 else { return false }; recreatedInputTemp = from
    return FileManager.default.createFile(atPath: from, contents: Data("synthetic replacement".utf8), attributes: [.posixPermissions: 0o600])
  })
  try installedInput.save(savedInput)
  expect(try Data(contentsOf: URL(fileURLWithPath: recreatedInputTemp!)) == Data("synthetic replacement".utf8))
  expect(try installedInput.load(scope: durableScope) == savedInput)
  var installRequestKey: String?; var recreatedRequestTemp: String?
  let installedRequest = ContextPrivateRequestStore(directory: root.appendingPathComponent("request-temp-install"), readKey: { installRequestKey }, saveKey: { installRequestKey = $0 }, replace: { from, to in
    guard rename(from, to) == 0 else { return false }; recreatedRequestTemp = from
    return FileManager.default.createFile(atPath: from, contents: Data("synthetic replacement".utf8), attributes: [.posixPermissions: 0o600])
  })
  try installedRequest.save(original)
  expect(try Data(contentsOf: URL(fileURLWithPath: recreatedRequestTemp!)) == Data("synthetic replacement".utf8))
  expect(try installedRequest.load(scope: durableScope).pending == original.pending)

  // Existing snapshots remain readable after a system clock rollback; future skew only rejects writes.
  let rollbackInput = ContextPrivateInputStore(directory: inputRoot, readKey: { inputKey }, saveKey: { _ in fatalError("rollback regenerated") }, now: { Date(timeIntervalSince1970: 1) })
  expect(try rollbackInput.load(scope: durableScope) == editedInput)
  do { try rollbackInput.save(savedInput); fatalError("future write accepted") } catch {}
  // Exactly 64 scopes: replacing a small existing snapshot remains allowed.
  try inputVault.save(savedInput)
  expect(try inputVault.load(scope: durableScope) == savedInput)
  var consumerKey: String?; var consumerRequestKey: String?
  let consumerVault = ContextPrivateInputStore(directory: root.appendingPathComponent("consumer-input"), readKey: { consumerKey }, saveKey: { consumerKey = $0 })
  let consumerRequests = ContextPrivateRequestStore(directory: root.appendingPathComponent("consumer-requests"), readKey: { consumerRequestKey }, saveKey: { consumerRequestKey = $0 })
  let consumer = ContextPrivateInbox(requestStore: consumerRequests, inputStore: consumerVault)
  var consumerHTTP = 0
  let authReply = try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"])
  consumer.chooseProject("", scope: { durableScope }); consumer.setInput("synthetic unsubmitted input"); consumer.inputTag = "question"
  consumer.saveInputOffline(scope: { durableScope })
  expect(consumer.inputSavedStatus == "saved" && consumerHTTP == 0)
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  expect(consumer.hasStagedInput && consumer.input.isEmpty && consumerHTTP == 0)
  expect(!consumer.reconcileDisplayIdentity(previous: nil, current: durableScope) && consumer.hasStagedInput)
  expect(consumer.reconcileDisplayIdentity(previous: nil, current: String(repeating: "c", count: 64)) && !consumer.hasStagedInput)
  consumer.stageInputOffline(scope: { durableScope })
  expect(consumer.hasStagedInput && consumerHTTP == 0)

  for malformed: [String: Any] in [
    ["version": true, "allowed": true, "meaning": "current-private-intake-authorization"],
    ["version": 1, "allowed": 1, "meaning": "current-private-intake-authorization"],
    ["version": 1, "allowed": false, "meaning": "current-private-intake-authorization"],
    ["version": 1, "allowed": true, "meaning": "current-private-intake-authorization", "text": "foreign"]
  ] {
    await consumer.restoreInput(request: { _, method, body in
      expect(method == "GET" && body == nil); return try bytes(malformed)
    }, scope: { durableScope })
    expect(consumer.input.isEmpty && consumer.hasStagedInput && consumer.inputSavedStatus == "unavailable")
  }
  await consumer.restoreInput(request: { path, method, body in
    consumerHTTP += 1; expect(path == "v1/context-private-authorization" && method == "GET" && body == nil)
    consumer.chooseProject("", scope: { durableScope }); consumer.setInput("typed while awaiting verification")
    return authReply
  }, scope: { durableScope })
  expect(consumer.input == "typed while awaiting verification" && consumer.inputSavedStatus == "unavailable")
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  var consumerScope = durableScope
  await consumer.restoreInput(request: { _, _, _ in consumerScope = String(repeating: "e", count: 64); return authReply }, scope: { consumerScope })
  expect(consumer.input.isEmpty && consumer.inputSavedStatus == "unavailable")
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  await consumer.restoreInput(request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { durableScope })
  expect(consumer.input.isEmpty && consumer.hasStagedInput)
  await consumer.restoreInput(request: { path, method, body in
    consumerHTTP += 1; expect(method == "GET" && body == nil); return authReply
  }, scope: { durableScope })
  expect(consumer.input == "synthetic unsubmitted input" && consumer.inputTag == "question" && consumer.inputSavedStatus == "restored")
  let originalEditorId = try consumerVault.load(scope: durableScope).editorId
  await consumer.load(request: { _, _, _ in try page([source]) }, scope: { durableScope })
  consumer.chooseProject("", scope: { durableScope }); consumer.setInput("synthetic unsubmitted input"); consumer.inputTag = "question"
  await consumer.reviewDraft(create: true, request: { _, _, _ in throw ContextPrivateInbox.Failure.invalid }, scope: { durableScope })
  expect(consumer.hasUnsubmittedRecovery && consumer.input.isEmpty)
  consumer.suspend()
  await consumer.recoverUnsubmitted(request: { _, method, body in expect(method == "GET" && body == nil); return authReply }, scope: { durableScope })
  expect(consumer.input == "synthetic unsubmitted input" && !consumer.hasUnsubmittedRecovery)
  await consumer.mutate(request: { _, method, body in
    consumerHTTP += 1; expect(method == "POST")
    let persisted = try consumerRequests.load(scope: durableScope)
    expect(!persisted.confirmed && persisted.pending.data == body && persisted.pending.inputEditorId == originalEditorId)
    expect(try consumerVault.load(scope: durableScope).boundRequestFingerprint != nil)
    throw ContextPrivateInbox.Failure.invalid
  }, scope: { durableScope })
  expect(consumer.writeUncertain)
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  expect(!consumer.hasStagedInput && consumer.input.isEmpty && consumer.inputSavedStatus == "pending")
  let pendingConsumer = try consumerRequests.load(scope: durableScope)
  try consumerRequests.save(.init(version: 1, scope: durableScope, pending: pendingConsumer.pending, confirmed: true))
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  expect(consumer.inputSavedStatus == "submitted" && !consumer.hasStagedInput)
  // Frozen source verification, no read of newer content into the saved editor.
  try consumerVault.save(editedInput)
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  var changedSource = source; changedSource["revision"] = editedInput.reference!.revision + 1
  await consumer.restoreInput(request: { _, method, body in expect(method == "GET" && body == nil); return try bytes(changedSource) }, scope: { durableScope })
  expect(consumer.input.isEmpty && consumer.inputSavedStatus == "unavailable")

  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  var originalSource = source
  originalSource["id"] = editedInput.reference!.id; originalSource["eventId"] = editedInput.reference!.eventId
  originalSource["revision"] = editedInput.reference!.revision
  originalSource["createdAt"] = editedInput.reference!.createdAt; originalSource["revisionChangedAt"] = editedInput.reference!.changedAt
  originalSource["projectId"] = NSNull(); originalSource["repositoryId"] = NSNull()
  await consumer.restoreInput(request: { path, method, body in
    expect(path == "v1/context-private/" + editedInput.reference!.id && method == "GET" && body == nil)
    return try bytes(originalSource)
  }, scope: { durableScope })
  expect(consumer.input == editedInput.text && consumer.editing?.revision == editedInput.reference!.revision)
  consumer.reset(); consumer.stageInputOffline(scope: { durableScope })
  await consumer.restoreInput(request: { _, _, _ in consumer.suspend(); return try bytes(originalSource) }, scope: { durableScope })
  expect(consumer.input.isEmpty && !consumer.hasStagedInput)
  // A binder failure leaves the original request durable and issues zero HTTP.
  let brokenInput = ContextPrivateInputStore(directory: root.appendingPathComponent("consumer-input"), readKey: { nil }, saveKey: { _ in fatalError("binder key regenerated") })
  var brokenRequestKey: String?
  let brokenRequests = ContextPrivateRequestStore(directory: root.appendingPathComponent("consumer-bind-fail"), readKey: { brokenRequestKey }, saveKey: { brokenRequestKey = $0 })
  let bindFail = ContextPrivateInbox(requestStore: brokenRequests, inputStore: brokenInput)
  bindFail.chooseProject("", scope: { durableScope }); bindFail.setInput("binder unsent"); var bindHTTP = 0
  await bindFail.mutate(request: { _, _, _ in bindHTTP += 1; return Data() }, scope: { durableScope })
  let bindRetained = try brokenRequests.load(scope: durableScope)
  expect(bindHTTP == 0 && bindFail.writeUncertain && !bindRetained.confirmed)
  // Missing snapshot is the sole not-found condition; corruption must not become absence.
  consumer.reset(); consumer.stageInputOffline(scope: { String(repeating: "c", count: 64) })
  expect(consumer.inputSavedStatus == "not-found")
  let consumerFile = root.appendingPathComponent("consumer-input/" + durableScope + ".v1.aes")
  try Data("corrupt".utf8).write(to: consumerFile)
  consumer.stageInputOffline(scope: { durableScope }); expect(consumer.inputSavedStatus == "unavailable")

  let unpaired = ContextPrivateInbox(inputStore: consumerVault)
  unpaired.chooseProject("", scope: { durableScope }); unpaired.setInput("unpaired input")
  expect(!unpaired.canSaveInput && !unpaired.canStageInput)
  var unpairedHTTP = 0
  await unpaired.mutate(request: { _, _, _ in unpairedHTTP += 1; return Data() }, scope: { durableScope })
  expect(unpairedHTTP == 0)
  // Static intermediate symlink is rejected before create/read; no outside path touched.
  let unsafeParent = root.appendingPathComponent("unsafe-input-parent")
  try FileManager.default.createSymbolicLink(at: unsafeParent, withDestinationURL: root.appendingPathComponent("consumer-input"))
  let unsafeInput = ContextPrivateInputStore(directory: unsafeParent.appendingPathComponent("Inputs"), readKey: { consumerKey }, saveKey: { _ in fatalError("unsafe key write") })
  do { try unsafeInput.save(savedInput); fatalError("intermediate symlink accepted") } catch {}
  expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("consumer-input/Inputs").path))
  do { _ = try unsafeInput.loadIfPresent(scope: durableScope); fatalError("unsafe ancestor interpreted as missing") } catch {}
  let unsafeRequests = ContextPrivateRequestStore(directory: unsafeParent.appendingPathComponent("Requests"), readKey: { consumerRequestKey }, saveKey: { _ in fatalError("unsafe request key write") })
  do { _ = try unsafeRequests.hasUnconfirmed(scope: durableScope); fatalError("unsafe request ancestor interpreted as absent") } catch {}
  print("unsubmitted input consumer offline save, read-only restore, epoch/scope guards and durable submission lineage passed")


  print("non-destructive archive, interrupted flush, rollback, stale snapshot, wrong key, capacity and typed response checks passed")

  print("durable request write-before-HTTP/restart/same-event/finalize-failure/key isolation scenarios passed")
  }

  do {
   let owner = String(repeating: "f", count: 64)
   var inputKey: String?, requestKey: String?
   let inputStore = ContextPrivateInputStore(directory: root.appendingPathComponent("project-input"), readKey: { inputKey }, saveKey: { inputKey = $0 })
   let requestStore = ContextPrivateRequestStore(directory: root.appendingPathComponent("project-request"), readKey: { requestKey }, saveKey: { requestKey = $0 })
   let catalog = try bytes(["version": 1, "projects": [["id": "vox", "name": "Vox", "repositoryId": "team/repo"]]])
   let auth = try bytes(["version": 1, "allowed": true, "meaning": "current-private-intake-authorization"])
   let model = ContextPrivateInbox(requestStore: requestStore, inputStore: inputStore)
   await model.loadProjects(request: { path, method, body in expect(path == "v1/context-projects" && method == "GET" && body == nil); return catalog }, scope: { owner })
   expect(model.projects.count == 1 && model.selectedProject == nil)
   model.chooseProject("vox", scope: { owner })
   model.setInput("synthetic scoped input", scope: { owner })
   model.setInputTag("question", scope: { owner })
   model.chooseProject("", scope: { owner })
   expect(model.inputAttribution.frozen && model.inputAttribution.project?.repositoryID == "team/repo" && !model.canChooseProject)
   model.saveInputOffline(scope: { owner })
   expect(model.inputSavedStatus == "saved")
   let restored = ContextPrivateInbox(requestStore: requestStore, inputStore: inputStore)
   restored.stageInputOffline(scope: { owner })
   var restoreReads = 0
   await restored.restoreInput(request: { path, method, body in
    expect(method == "GET" && body == nil); restoreReads += 1
    return path == "v1/context-projects" ? catalog : auth
   }, scope: { owner })
   expect(restoreReads == 2 && restored.input == "synthetic scoped input" && restored.inputAttribution.project?.repositoryID == "team/repo")
   let remapped = ContextPrivateInbox(requestStore: requestStore, inputStore: inputStore)
   remapped.stageInputOffline(scope: { owner })
   await remapped.restoreInput(request: { path, method, body in
    expect(method == "GET" && body == nil)
    if path == "v1/context-projects" { return try bytes(["version": 1, "projects": [["id": "vox", "name": "Vox", "repositoryId": "team/remapped"]]]) }
    return auth
   }, scope: { owner })
   expect(remapped.input.isEmpty && remapped.hasStagedInput && remapped.inputSavedStatus == "unavailable")
   var posts = 0
   await restored.mutate(request: { path, method, body in
    if method == "GET" { return path == "v1/context-projects" ? catalog : auth }
    expect(method == "POST"); posts += 1
    let payload = try JSONSerialization.jsonObject(with: body!) as! [String: Any]
    expect(payload["projectId"] as? String == "vox" && payload["expectedRepositoryId"] as? String == "team/repo")
    var reply = source; reply["eventId"] = payload["eventId"]; reply["projectId"] = "vox"; reply["repositoryId"] = "team/repo"
    reply["text"] = payload["text"]; reply["tags"] = payload["tags"]
    return try bytes(reply)
   }, scope: { owner })
   expect(posts == 1 && restored.input.isEmpty && !restored.inputAttribution.frozen && restored.requestRecoveryStatus == "confirmed")
   let unknown = ContextPrivateInbox(requestStore: requestStore)
   await unknown.loadProjects(request: { _, _, _ in catalog }, scope: { owner })
   unknown.chooseProject("vox", scope: { owner }); unknown.setInput("unknown synthetic input", scope: { owner })
   await unknown.mutate(request: { path, method, _ in
    if method == "GET" { return path == "v1/context-projects" ? catalog : auth }
    expect(method == "POST"); throw NSError(domain: "synthetic", code: 1)
   }, scope: { owner })
   expect(unknown.writeUncertain)
   var probes = 0
   await unknown.retryUncertain(request: { _, method, body in
    expect(method == "GET" && body == nil); probes += 1
    return try bytes(["version": 1, "meaning": "point-in-time-only-not-absence-proof", "status": "not-observed", "source": NSNull(), "mutationId": NSNull()])
   }, scope: { owner })
   expect(probes == 1 && unknown.writeUncertain && unknown.requestRecoveryStatus == "unknown")
   let legacyScope = ContextPrivateInbox(requestStore: requestStore, inputStore: inputStore)
   let legacyCipher = try Data(contentsOf: root.appendingPathComponent("project-input/" + owner + ".v1.aes"))
   legacyScope.setInput("legacy text without a scope")
   await legacyScope.mutate(request: { _, _, _ in preconditionFailure("nil frozen scope dispatched") }, scope: { owner })
   expect(legacyScope.failed && !legacyScope.writeUncertain && legacyScope.input == "legacy text without a scope")
   legacyScope.saveInputOffline(scope: { owner })
   expect(legacyScope.inputSavedStatus == "unavailable")
   expect(try Data(contentsOf: root.appendingPathComponent("project-input/" + owner + ".v1.aes")) == legacyCipher)
   let unassigned = ContextPrivateInbox()
   await unassigned.loadProjects(request: { _, _, _ in catalog }, scope: { owner })
   unassigned.setInput("unassigned", scope: { owner }); unassigned.chooseProject("vox", scope: { owner })
   expect(unassigned.inputAttribution.frozen && unassigned.inputAttribution.project == nil)
   unassigned.newInput(); expect(!unassigned.inputAttribution.frozen && unassigned.selectedProject == nil)
   var switched = owner
   await unassigned.loadProjects(request: { _, _, _ in switched = String(repeating: "e", count: 64); return catalog }, scope: { switched })
   expect(unassigned.projects.isEmpty && unassigned.projectStatus == "unavailable")

   let tagged = ContextPrivateInbox()
   await tagged.loadProjects(request: { _, _, _ in catalog }, scope: { owner })
   tagged.chooseProject("vox", scope: { owner }); tagged.setInputTag("question", scope: { owner })
   tagged.setInputTag("", scope: { owner }); tagged.chooseProject("", scope: { owner })
   expect(tagged.inputAttribution.frozen && tagged.inputAttribution.project?.id == "vox")
   let savedProject = ContextPrivateSavedInput(version: 1, scope: owner, text: "revoked input", tag: "", reference: nil, projectId: "vox", repositoryId: "team/repo", savedAt: stamp)
   // Isolate revocation recovery from the unknown pending request above.
   let revokedRequests = ContextPrivateRequestStore(directory: root.appendingPathComponent("revoked-requests"), readKey: { nil }, saveKey: { _ in })
   let revokedReader = ContextPrivateInbox(requestStore: revokedRequests, inputStore: inputStore)
   try inputStore.save(savedProject); revokedReader.stageInputOffline(scope: { owner })
   await revokedReader.restoreInput(request: { path, method, _ in
    expect(method == "GET")
    return path == "v1/context-projects" ? try bytes(["version": 1, "projects": []]) : auth
   }, scope: { owner })
   expect(revokedReader.input.isEmpty && revokedReader.hasStagedInput)
   let malformed = ContextPrivateInbox()
   await malformed.loadProjects(request: { _, _, _ in try bytes(["version": 1, "projects": [["id": "vox", "name": "Vox", "repositoryId": "team/repo"], ["id": "vox", "name": "Vox", "repositoryId": "team/repo"]]]) }, scope: { owner })
   expect(malformed.projects.isEmpty && malformed.projectStatus == "unavailable")

   let preflight = ContextPrivateInbox(requestStore: revokedRequests)
   await preflight.loadProjects(request: { _, _, _ in catalog }, scope: { owner })
   preflight.chooseProject("vox", scope: { owner }); preflight.setInput("stale before submission", scope: { owner })
   var preflightReads = 0
   await preflight.mutate(request: { path, method, _ in
    expect(method == "GET"); preflightReads += 1
    return path == "v1/context-projects" ? try bytes(["version": 1, "projects": []]) : auth
   }, scope: { owner })
   expect(preflightReads == 2 && preflight.failed && !preflight.writeUncertain && preflight.input == "stale before submission")
   let preflightPending = try revokedRequests.hasUnconfirmed(scope: owner)
   expect(!preflightPending)

   let partialInput = ContextPrivateSavedInput(version: 1, scope: owner, text: "partial snapshot", tag: "", reference: nil, projectId: "vox", repositoryId: nil, savedAt: stamp)
   expect(!partialInput.valid)
   let guardedBody = try bytes(["eventId":"guarded-event","visibility":"private","text":"guarded","tags":[],"projectId":"vox","expectedRepositoryId":"team/repo"])
   let partialPending = ContextPrivateInbox.PendingWrite(path:"v1/context-private",method:"POST",data:guardedBody,mutationId:nil,selected:nil,withdrawn:false,eventId:"guarded-event",text:"guarded",tags:[],projectId:"vox",repositoryId:nil)
   expect(!ContextPrivateSavedRequest(version:1,scope:owner,pending:partialPending,confirmed:false).valid)
   let raceRequests = ContextPrivateRequestStore(directory:root.appendingPathComponent("race-requests"),readKey:{ requestKey },saveKey:{ requestKey=$0 })
   let race = ContextPrivateInbox(requestStore:raceRequests)
   await race.loadProjects(request:{ _,_,_ in catalog },scope:{ owner })
   race.chooseProject("vox",scope:{ owner });race.setInput("retain snapshot after server remap",scope:{ owner })
   var racePosts=0
   await race.mutate(request:{ path,method,_ in
    if method=="GET" { return path=="v1/context-projects" ? catalog : auth }
    racePosts+=1;throw ContextPrivateHTTPFailure(status:409,data:try bytes(["detail":"PRIVATE_CONTEXT_PROJECT_CHANGED"]))
   },scope:{ owner })
   expect(racePosts==1 && race.writeUncertain && race.input=="retain snapshot after server remap" && race.serverReplyCode=="PRIVATE_CONTEXT_PROJECT_CHANGED")
   await race.retryUncertain(request:{ _,method,_ in
    expect(method=="GET")
    return try bytes(["version":1,"meaning":"point-in-time-only-not-absence-proof","status":"not-observed","source":NSNull(),"mutationId":NSNull()])
   },scope:{ owner })
   expect(racePosts==1 && race.writeUncertain)
   print("project picker, frozen assigned/unassigned scopes, encrypted recovery/remap refusal and GET-only unknown reconciliation passed")
  }
  do {
   let owner = String(repeating: "f", count: 64), other = String(repeating: "e", count: 64)
   let noStoreBrowser = ContextPrivateInbox()
   noStoreBrowser.browseArchiveFiles(scope: { owner })
   expect(noStoreBrowser.archiveFilesStatus == "unavailable" && noStoreBrowser.archiveFiles.isEmpty)
   let inventoryRoot = root.appendingPathComponent("archive-inventory")
   var forbiddenKeyCalls = 0
   let inventory = ContextPrivateRequestStore(directory: inventoryRoot,
    readKey: { forbiddenKeyCalls += 1; throw ContextPrivateRequestStore.Failure.unavailable },
    saveKey: { _ in preconditionFailure("archive browse created key") })
   expect(try inventory.listArchiveFiles(scope: owner).isEmpty)
   expect(!FileManager.default.fileExists(atPath: inventoryRoot.path) && forbiddenKeyCalls == 0)
   try FileManager.default.createDirectory(at: inventoryRoot, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   expect(try inventory.listArchiveFiles(scope: owner).isEmpty)
   let archive = inventoryRoot.appendingPathComponent("Archived")
   try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   let label = String(repeating: "a", count: 64)
   let ownFile = archive.appendingPathComponent(owner + "." + label + ".v1.aes")
   let otherFile = archive.appendingPathComponent(other + "." + String(repeating: "b", count: 64) + ".v1.aes")
   // Synthetic non-cipher bytes: listing must not pretend to authenticate contents.
   let originalBytes = Data("synthetic opaque bytes".utf8)
   expect(FileManager.default.createFile(atPath: ownFile.path, contents: originalBytes, attributes: [.posixPermissions: 0o600]))
   expect(FileManager.default.createFile(atPath: otherFile.path, contents: Data("other scope bytes".utf8), attributes: [.posixPermissions: 0o600]))
   let list = try inventory.listArchiveFiles(scope: owner)
   expect(list == [.init(id: label, bytes: originalBytes.count)] && forbiddenKeyCalls == 0)
   let missingKeyStore = ContextPrivateRequestStore(directory: inventoryRoot, readKey: { nil }, saveKey: { _ in preconditionFailure("diagnostic created key") })
   let invalidKeyStore = ContextPrivateRequestStore(directory: inventoryRoot, readKey: { "invalid" }, saveKey: { _ in preconditionFailure("diagnostic replaced key") })
   let availableKeyStore = ContextPrivateRequestStore(directory: inventoryRoot, readKey: { Data(repeating: 1, count: 32).base64EncodedString() }, saveKey: { _ in preconditionFailure("diagnostic key mutation") })
   let deniedKeyStore = ContextPrivateRequestStore(directory: inventoryRoot, readKey: { throw ContextPrivateRequestStore.Failure.unavailable }, saveKey: { _ in preconditionFailure("denied key replaced") })
   expect(deniedKeyStore.inspectArchiveKey(scope: owner) == .unavailable)
   expect(missingKeyStore.inspectArchiveKey(scope: owner) == .missing)
   expect(invalidKeyStore.inspectArchiveKey(scope: owner) == .invalid)
   expect(availableKeyStore.inspectArchiveKey(scope: owner) == .present)
   expect(availableKeyStore.inspectArchiveKey(scope: "../escape") == .unavailable)
   let noKeyBrowser = ContextPrivateInbox(requestStore: missingKeyStore)
   noKeyBrowser.browseArchiveFiles(scope: { owner }); await noKeyBrowser.inspectArchiveKey(scope: { owner })
   expect(noKeyBrowser.archiveKeyStatus == .missing)
   await noKeyBrowser.inspectArchiveKey(scope: { other })
   expect(noKeyBrowser.archiveKeyStatus == nil && noKeyBrowser.archiveFiles.isEmpty)
   expect(try !inventory.archiveFingerprintMatches(scope: owner, label: label))
   let matchingLabel = SHA256.hash(data: originalBytes).map { String(format: "%02x", $0) }.joined()
   let matchingFile = archive.appendingPathComponent(owner + "." + matchingLabel + ".v1.aes")
   expect(FileManager.default.createFile(atPath: matchingFile.path, contents: originalBytes, attributes: [.posixPermissions: 0o600]))
   expect(try inventory.archiveFingerprintMatches(scope: owner, label: matchingLabel))
   do { _ = try inventory.archiveFingerprintMatches(scope: other, label: matchingLabel); preconditionFailure("foreign archive fingerprint accepted") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: "../escape"); preconditionFailure("archive fingerprint path accepted") } catch {}
   do { _ = try availableKeyStore.inspectAuthenticatedArchive(scope: owner, label: matchingLabel); preconditionFailure("opaque bytes authenticated") } catch {}
   let syntheticKey = SymmetricKey(data: Data(repeating: 1, count: 32))
   let event = "archive-auth-event"
   let pending = ContextPrivateInbox.PendingWrite(path: "v1/context-private", method: "POST", data: try bytes(["eventId": event, "visibility": "private", "text": "synthetic private", "tags": []]), mutationId: nil, selected: nil, withdrawn: false, eventId: event, text: "synthetic private", tags: [], projectId: nil, repositoryId: nil)
   let saved = ContextPrivateSavedRequest(version: 1, scope: owner, pending: pending, confirmed: false)
   let aad = Data(("vox-private-request/v1|" + owner).utf8)
   let cipher = try AES.GCM.seal(JSONEncoder().encode(saved), using: syntheticKey, authenticating: aad).combined!
   let cipherLabel = SHA256.hash(data: cipher).map { String(format: "%02x", $0) }.joined()
   let cipherPath = archive.appendingPathComponent(owner + "." + cipherLabel + ".v1.aes")
   expect(FileManager.default.createFile(atPath: cipherPath.path, contents: cipher, attributes: [.posixPermissions: 0o600]))
   let checked = try availableKeyStore.inspectAuthenticatedArchive(scope: owner, label: cipherLabel)
   expect(checked.cipherFingerprint == cipherLabel && !checked.confirmed)
   expect(checked.eventFingerprint == SHA256.hash(data: Data((event + "|").utf8)).map { String(format: "%02x", $0) }.joined())
   func rejectCipher(_ data: Data, under scope: String, authentication: Bool = false) throws {
    let label = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    let path = archive.appendingPathComponent(scope + "." + label + ".v1.aes")
    expect(FileManager.default.createFile(atPath: path.path, contents: data, attributes: [.posixPermissions: 0o600]))
    do { _ = try availableKeyStore.inspectAuthenticatedArchive(scope: scope, label: label); preconditionFailure("invalid archive authenticated") } catch CryptoKitError.authenticationFailure { expect(authentication) } catch ContextPrivateRequestStore.Failure.invalid { expect(!authentication) } catch { preconditionFailure("unexpected archive rejection") }
    expect(try Data(contentsOf: path) == data)
    try FileManager.default.removeItem(at: path)
   }
   try rejectCipher(cipher, under: other, authentication: true) // Correct filename, wrong AAD: reaches authentication.
   let invalidObject = try JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as! [String: Any]
   for kind in 0..<4 {
    var object = invalidObject
    if kind == 0 { object["scope"] = other }
    if kind == 1 { object["extra"] = true }
    if kind == 2 { object["version"] = 2 }
    if kind == 3 { var body = object["pending"] as! [String: Any]; body["method"] = "DELETE"; object["pending"] = body }
    let sealed = try AES.GCM.seal(bytes(object), using: syntheticKey, authenticating: aad).combined!
    try rejectCipher(sealed, under: owner)
   }
   let authenticatedBrowser = ContextPrivateInbox(requestStore: availableKeyStore)
   authenticatedBrowser.browseArchiveFiles(scope: { owner })
   await authenticatedBrowser.authenticateArchive(label: cipherLabel, scope: { owner })
   expect(authenticatedBrowser.archiveAuthenticationStatus[cipherLabel] == "authenticated")
   authenticatedBrowser.browseArchiveFiles(scope: { owner })
   expect(authenticatedBrowser.archiveAuthenticationStatus.isEmpty)
   var authScopeReads = 0
   await authenticatedBrowser.authenticateArchive(label: cipherLabel, scope: { authScopeReads += 1; return authScopeReads == 1 ? owner : other })
   expect(authenticatedBrowser.archiveAuthenticationStatus.isEmpty && authenticatedBrowser.archiveFiles.isEmpty)
   authenticatedBrowser.browseArchiveFiles(scope: { owner }); authenticatedBrowser.reset()
   expect(authenticatedBrowser.archiveAuthenticationStatus.isEmpty)
   let wrongKey = ContextPrivateRequestStore(directory: inventoryRoot, readKey: { Data(repeating: 2, count: 32).base64EncodedString() }, saveKey: { _ in preconditionFailure("wrong key replaced") })
   do { _ = try wrongKey.inspectAuthenticatedArchive(scope: owner, label: cipherLabel); preconditionFailure("wrong valid key accepted") } catch {}
   var tampered = cipher; tampered[tampered.count - 1] ^= 1
   try rejectCipher(tampered, under: owner, authentication: true) // Recomputed filename hash cannot bypass GCM.

   expect(try Data(contentsOf: cipherPath) == cipher && !FileManager.default.fileExists(atPath: inventoryRoot.appendingPathComponent(".vault.lock").path))
   for store in [missingKeyStore, invalidKeyStore, deniedKeyStore] {
    do { _ = try store.inspectAuthenticatedArchive(scope: owner, label: cipherLabel); preconditionFailure("unavailable key accepted") } catch {}
   }
   do { _ = try availableKeyStore.inspectAuthenticatedArchive(scope: other, label: cipherLabel); preconditionFailure("foreign archive authenticated") } catch {}
   try FileManager.default.removeItem(at: cipherPath) // Synthetic fixture only.
   try FileManager.default.removeItem(at: matchingFile) // Disposable synthetic fixture only.
   expect(forbiddenKeyCalls == 0)
   let browser = ContextPrivateInbox(requestStore: inventory)
   browser.browseArchiveFiles(scope: { owner })
   expect(browser.archiveFiles == list && browser.archiveFilesStatus == "loaded" && browser.archiveFilesAreCurrent(scope: { owner }))
   expect(!browser.archiveFilesAreCurrent(scope: { other }))
   await browser.inspectArchiveFingerprint(label: label, scope: { owner })
   expect(browser.archiveFingerprintStatus[label] == "mismatch" && forbiddenKeyCalls == 0)
   await browser.inspectArchiveFingerprint(label: label, scope: { other })
   expect(browser.archiveFingerprintStatus.isEmpty && browser.archiveFiles.isEmpty && browser.archiveFilesStatus == "unavailable")

   browser.browseArchiveFiles(scope: { other })
   expect(browser.archiveFiles.count == 1 && browser.archiveFiles[0].id == String(repeating: "b", count: 64) && browser.archiveFilesStatus == "loaded" && browser.archiveFilesAreCurrent(scope: { other }))
   browser.reset(); var scopeReads = 0
   browser.browseArchiveFiles(scope: { scopeReads += 1; return scopeReads == 1 ? owner : other })
   expect(browser.archiveFiles.isEmpty && browser.archiveFilesStatus == "unavailable")
   browser.reset(); browser.browseArchiveFiles(scope: { owner }); browser.suspend()
   expect(browser.archiveFiles.isEmpty && browser.archiveFilesStatus.isEmpty)
   expect(try Data(contentsOf: ownFile) == originalBytes && !FileManager.default.fileExists(atPath: inventoryRoot.appendingPathComponent(".vault.lock").path))
   expect(chmod(ownFile.path, 0o644) == 0)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("unsafe archive metadata exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: unsafe archive metadata exposed") } catch {}
   expect(chmod(ownFile.path, 0o600) == 0)
   let linkName = archive.appendingPathComponent(owner + "." + String(repeating: "c", count: 64) + ".v1.aes")
   try FileManager.default.createSymbolicLink(at: linkName, withDestinationURL: ownFile)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("symlink archive exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: symlink archive exposed") } catch {}
   try FileManager.default.removeItem(at: linkName) // This fixture's synthetic symlink only.
   let hardName = archive.appendingPathComponent(owner + "." + String(repeating: "d", count: 64) + ".v1.aes")
   expect(link(ownFile.path, hardName.path) == 0)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("hardlink archive exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: hardlink archive exposed") } catch {}
   try FileManager.default.removeItem(at: hardName)
   expect(chmod(otherFile.path, 0o644) == 0)
   expect(try inventory.listArchiveFiles(scope: owner) == list) // Other-scope unsafe bytes are not stat'ed.
   expect(chmod(ownFile.path, 0o400) == 0)
   expect(try inventory.listArchiveFiles(scope: owner) == list) // Read-only owner mode follows store policy.
   expect(chmod(ownFile.path, 0o600) == 0)
   let ownFD = open(ownFile.path, O_WRONLY | O_NOFOLLOW)
   expect(ownFD >= 0)
   expect(ftruncate(ownFD, 0) == 0)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("zero-byte archive exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: zero-byte archive exposed") } catch {}
   expect(ftruncate(ownFD, 2_000_000) == 0)
   expect(try inventory.listArchiveFiles(scope: owner)[0].bytes == 2_000_000)
   expect(ftruncate(ownFD, 2_000_001) == 0)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("oversized archive exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: oversized archive exposed") } catch {}
   close(ownFD); try originalBytes.write(to: ownFile)
   expect(mkfifo(linkName.path, 0o600) == 0)
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("FIFO archive exposed") } catch {}
   do { _ = try inventory.archiveFingerprintMatches(scope: owner, label: label); preconditionFailure("unsafe fingerprint input: FIFO archive exposed") } catch {}
   try FileManager.default.removeItem(at: linkName)
   let missingArchive = root.appendingPathComponent("missing-archive-root")
   try FileManager.default.createDirectory(at: missingArchive, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   let missingStore = ContextPrivateRequestStore(directory: missingArchive, readKey: { preconditionFailure("missing archive read key") }, saveKey: { _ in preconditionFailure("missing archive write key") })
   let missingPath = missingArchive.appendingPathComponent("Archived")
   try FileManager.default.createSymbolicLink(at: missingPath, withDestinationURL: archive)
   do { _ = try missingStore.listArchiveFiles(scope: owner); preconditionFailure("archive directory symlink accepted") } catch {}
   try FileManager.default.removeItem(at: missingPath)
   expect(FileManager.default.createFile(atPath: missingPath.path, contents: Data("not directory".utf8), attributes: [.posixPermissions: 0o600]))
   do { _ = try missingStore.listArchiveFiles(scope: owner); preconditionFailure("archive directory regular file accepted") } catch {}
   for index in 0..<254 {
    expect(FileManager.default.createFile(atPath: archive.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
   }
   expect(try inventory.listArchiveFiles(scope: owner) == list) // Exactly256 direct entries.
   expect(FileManager.default.createFile(atPath: archive.appendingPathComponent("foreign-254").path, contents: Data(), attributes: [.posixPermissions: 0o600]))
   do { _ = try inventory.listArchiveFiles(scope: owner); preconditionFailure("unbounded archive browse") } catch ContextPrivateRequestStore.Failure.capacity {} catch { preconditionFailure("wrong archive capacity status") }
   browser.reset(); browser.browseArchiveFiles(scope: { owner })
   expect(browser.archiveFiles.isEmpty && browser.archiveFilesStatus == "capacity" && forbiddenKeyCalls == 0)
   expect(try Data(contentsOf: ownFile) == originalBytes)
   try FileManager.default.removeItem(at: missingPath)
   try FileManager.default.createDirectory(at: missingPath, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   for index in 0..<64 {
    let nonce = String(format: "%064x", index)
    expect(FileManager.default.createFile(atPath: missingPath.appendingPathComponent(other + "." + nonce + ".v1.aes").path, contents: Data("opaque".utf8), attributes: [.posixPermissions: 0o600]))
   }
   expect(try missingStore.listArchiveFiles(scope: owner).isEmpty) // Exactly64 snapshots, other-scope contents never touched.
   let sorted = try missingStore.listArchiveFiles(scope: other)
   expect(sorted.count == 64 && sorted.first?.id == String(repeating: "0", count: 64) && sorted.map(\.id) == sorted.map(\.id).sorted())
   expect(FileManager.default.createFile(atPath: missingPath.appendingPathComponent(other + "." + String(repeating: "f", count: 64) + ".v1.aes").path, contents: Data("opaque".utf8), attributes: [.posixPermissions: 0o600]))
   do { _ = try missingStore.listArchiveFiles(scope: owner); preconditionFailure("archive snapshot cap ignored") } catch ContextPrivateRequestStore.Failure.capacity {} catch { preconditionFailure("wrong snapshot capacity status") }
   print("scope-only archive listing and explicit ciphertext fingerprint diagnostics passed; no keys/decryption/writes or transport")
  }
  print("PASS: \(assertionCount) expect assertions in isolated production private consumer/client harness; failure-path traps also exercised")
  print("private production consumer/client scenarios passed; no network, Keychain, history or backup writes")
 }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vox-private-inbox-') as temporary:
        work = Path(temporary)
        (work / 'Dependencies.swift').write_text(STUB.replace("static func load(key:", "static func addSecureScalarIfAbsent(key: String, value: String) throws {}\n static func loadSecureScalarStrict(key: String) throws -> String? { nil }\n static func load(key:"))
        (work / 'Check.swift').write_text(HARNESS)
        binary = work / 'check'
        subprocess.run(['swiftc', '-sdk', args.sdk, '-swift-version', '5', '-parse-as-library',
                        str(repo / 'macos/VoxLog/Services/InputBackupClient.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextWeeklySources.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextPrivateInbox.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextPrivateDraftStore.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextPrivateRequestStore.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextPrivateInputStore.swift'),
                        str(work / 'Dependencies.swift'), str(work / 'Check.swift'), '-o', str(binary)], check=True)
        subprocess.run([str(binary), str(work / 'queue'), str(repo / 'tests/fixtures/context-private-v1.json')], check=True)


if __name__ == '__main__':
    main()
