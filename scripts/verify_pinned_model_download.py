"""Production downloader with synthetic transport/URLProtocol; never sends a network request."""
import argparse
from pathlib import Path
import subprocess
import select
import signal
import tempfile

HARNESS = r'''
import Foundation
import Darwin
final class Fixture: URLProtocol {
 static var status = 200, payload = Data("abc".utf8), encoding: String? = nil
 static var requests = 0, suspended = false, redirecting = false, beforeHeaders = false
 static var chunks: [Data]? = nil
 override class func canInit(with request: URLRequest) -> Bool { true }
 override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
 override func startLoading() {
  Self.requests += 1
  if Self.beforeHeaders { return }
  if Self.redirecting {
   let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": "https://fixture.invalid/forbidden"])!
   client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: URL(string: "https://fixture.invalid/forbidden")!), redirectResponse: response)
   return
  }
  var headers = ["Content-Length": "3"]
  if let encoding = Self.encoding { headers["Content-Encoding"] = encoding }
  client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: "HTTP/1.1", headerFields: headers)!, cacheStoragePolicy: .notAllowed)
  if Self.suspended { return }
  for chunk in Self.chunks ?? [Self.payload] { client?.urlProtocol(self, didLoad: chunk) }
  client?.urlProtocolDidFinishLoading(self)
 }
 override func stopLoading() {}
}
final class ProgressCapture: @unchecked Sendable {
 let lock = NSLock()
 private var values: [PinnedModelDownload.Progress] = []
 private var callback: PinnedModelDownload.Reporter?
 func append(_ value: PinnedModelDownload.Progress) { lock.lock(); values.append(value); lock.unlock() }
 func snapshots() -> [PinnedModelDownload.Progress] { lock.lock(); defer { lock.unlock() }; return values }
 func keep(_ report: @escaping PinnedModelDownload.Reporter) { lock.lock(); callback = report; lock.unlock() }
 func sendOld(_ snapshot: PinnedModelDownload.Progress) { lock.lock(); let report = callback; lock.unlock(); report?(snapshot) }
}
@main struct Check {
 @MainActor static func main() async throws {
  let parent = URL(fileURLWithPath: CommandLine.arguments[1])
  let fm = FileManager.default, data = Data("abc".utf8), hash = ModelManifest.hash(Data("abc".utf8))
  let canonical = "[{\"path\":\"config.json\",\"sha256\":\"\(hash)\",\"size\":3}]"
  let manifest = try ModelManifest(files: [.init(path: "config.json", size: 3, sha256: hash)], expectedDigest: ModelManifest.hash(Data(canonical.utf8)))
  let mirror = URL(string: "https://fixture.invalid/pinned")!
  if CommandLine.arguments.count > 2 {
   let mode = CommandLine.arguments[2]
   if mode == "busy" {
    do { _ = try VerifiedModelDownloadCache(parent: parent, mirror: mirror, manifest: manifest); preconditionFailure("cross-process lease accepted") }
    catch VerifiedModelDownloadCache.Failure.busy { print("BUSY"); return }
   }
   let cache = try VerifiedModelDownloadCache(parent: parent, mirror: mirror, manifest: manifest)
   if mode == "hold" {
    let source = parent.appendingPathComponent("synthetic-source")
    try fm.createDirectory(at: source, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    try data.write(to: source.appendingPathComponent("config.json"))
    try fm.setAttributes([.posixPermissions: 0o444], ofItemAtPath: source.appendingPathComponent("config.json").path)
    try cache.retain(manifest.files[0], from: source)
    var sourceInfo = stat()
    precondition(lstat(source.appendingPathComponent("config.json").path, &sourceInfo) == 0 && sourceInfo.st_mode & 0o777 == 0o444)
    print("READY"); fflush(stdout)
    _ = readLine()
    withExtendedLifetime(cache) {}
    return
   }
   let retained = try cache.contains(manifest.files[0])
   precondition(mode == "reopen" && retained)
   print("REOPENED_VERIFIED"); return
  }

  let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [Fixture.self]
  let http: PinnedModelDownload.Transport = { url, size, emit in
   try await PinnedModelDownload.stream(url: url, maximum: size, emit: emit, configuration: config)
  }
  func run(_ transport: PinnedModelDownload.Transport) async throws -> URL {
   try await PinnedModelDownload.download(mirror: mirror, parent: parent, manifest: manifest, transport: transport)
  }
  func rejects(_ expected: String, _ operation: () async throws -> URL) async throws {
   do { _ = try await operation(); preconditionFailure("invalid accepted") } catch {
    let kind: String
    switch error {
    case PinnedModelDownload.Failure.response: kind = "response"
    case PinnedModelDownload.Failure.oversized: kind = "oversized"
    case PinnedModelDownload.Failure.incomplete: kind = "incomplete"
    case ModelTreeVerifier.Failure.hashMismatch: kind = "hash"
    default: kind = "unexpected"
    }
    precondition(kind == expected, "wrong failure classification")
   }
   precondition(try! fm.contentsOfDirectory(atPath: parent.path).isEmpty)
  }
  let verified = try await run(http)
  precondition(try! Data(contentsOf: verified.appendingPathComponent("config.json")) == data)
  try fm.removeItem(at: verified)
  Fixture.status = 302; try await rejects("response") { try await run(http) }
  Fixture.status = 503; try await rejects("response") { try await run(http) }
  Fixture.status = 200; Fixture.encoding = "gzip"; try await rejects("response") { try await run(http) }
  Fixture.encoding = nil; Fixture.payload = Data("abcdef".utf8); try await rejects("oversized") { try await run(http) }
  Fixture.payload = Data("a".utf8); try await rejects("incomplete") { try await run(http) }
  Fixture.payload = Data("xyz".utf8); try await rejects("hash") { try await run(http) }
  try await rejects("oversized") { try await run { _, _, emit in try emit(Data("abcdef".utf8)) } }
  let cancelled = await Task { () -> Bool in
   do {
    _ = try await run { _, _, emit in
     try emit(data)
     withUnsafeCurrentTask { $0?.cancel() }
    }
    return false
   } catch is CancellationError { return true } catch { return false }
  }.value
  precondition(cancelled && (try! fm.contentsOfDirectory(atPath: parent.path).isEmpty))
  for bad in ["http://fixture.invalid", "https://user:pass@fixture.invalid", "https://fixture.invalid?a=b", "https://fixture.invalid/#x"] {
   do { try PinnedModelDownload.validateMirror(URL(string: bad)!); preconditionFailure("unsafe URL accepted") } catch {}
  }
  precondition(Fixture.requests == 7)
  Fixture.suspended = true
  let pending = Task { try await run(http) }
  for _ in 0..<200 {
   if Fixture.requests == 8 { break }
   try await Task.sleep(nanoseconds: 10_000_000)
  }
  precondition(Fixture.requests == 8)
  pending.cancel()
  do { _ = try await pending.value; preconditionFailure("cancelled request succeeded") }
  catch is CancellationError {} catch { preconditionFailure("wrong cancellation classification") }
  precondition(try! fm.contentsOfDirectory(atPath: parent.path).isEmpty)
  Fixture.suspended = false; Fixture.beforeHeaders = true
  let beforeHeaders = Fixture.requests
  let waitingHeaders = Task { try await run(http) }
  for _ in 0..<200 {
   if Fixture.requests == beforeHeaders + 1 { break }
   try await Task.sleep(nanoseconds: 10_000_000)
  }
  precondition(Fixture.requests == beforeHeaders + 1)
  waitingHeaders.cancel()
  do { _ = try await waitingHeaders.value; preconditionFailure("pre-header cancellation succeeded") }
  catch is CancellationError {} catch { preconditionFailure("wrong pre-header cancellation") }
  precondition(try! fm.contentsOfDirectory(atPath: parent.path).isEmpty)
  Fixture.beforeHeaders = false
  Fixture.chunks = [Data("a".utf8), Data("b".utf8), Data("c".utf8)]
  let split = try await run(http)
  precondition(try! Data(contentsOf: split.appendingPathComponent("config.json")) == data)
  try fm.removeItem(at: split)
  Fixture.chunks = [Data("ab".utf8), Data("cd".utf8)]
  try await rejects("oversized") { try await run(http) }
  Fixture.chunks = nil
  // A disk-writer failure terminates the delegate stream and keeps its exact error.
  var emitted = 0
  do {
   try await PinnedModelDownload.stream(url: mirror, maximum: 3, emit: { _ in
    emitted += 1; throw ModelTreeVerifier.Failure.writeFailed
   }, configuration: config)
   preconditionFailure("writer failure ignored")
  } catch ModelTreeVerifier.Failure.writeFailed {}
  precondition(emitted == 1)
  Fixture.redirecting = true
  let beforeRedirect = Fixture.requests
  do { _ = try await run(http); preconditionFailure("redirect accepted") }
  catch PinnedModelDownload.Failure.response {} catch { preconditionFailure("wrong redirect failure") }
  precondition(Fixture.requests == beforeRedirect + 1)
  precondition(try! fm.contentsOfDirectory(atPath: parent.path).isEmpty)
  // A broken installed store must fail before any transport request.
  let requestsBefore = Fixture.requests
  let vox = parent.appendingPathComponent("VoxLog")
  try fm.createDirectory(at: vox, withIntermediateDirectories: false)
  let sentinel = vox.appendingPathComponent("Components")
  try Data("sentinel".utf8).write(to: sentinel)
  do { _ = try await PinnedModelDownload.qwen(mirror: mirror, parent: parent); preconditionFailure("broken store ignored") }
  catch ModelTreeVerifier.Failure.unsafeRoot {}
  precondition(Fixture.requests == requestsBefore && (try! Data(contentsOf: sentinel) == Data("sentinel".utf8)))
  // Missing revision inside a safe existing store is installable, unlike corrupt bytes.
  try fm.removeItem(at: sentinel)
  try fm.createDirectory(at: sentinel, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  precondition(try! !PinnedModelDownload.installedModel(in: sentinel, modelID: "fixture", manifest: manifest))
  let fixtureSource = parent.appendingPathComponent("fixture-source")
  try fm.createDirectory(at: fixtureSource, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  try data.write(to: fixtureSource.appendingPathComponent("config.json"))
  let installed = try ModelStore(root: sentinel).importModel(source: fixtureSource, modelID: "fixture", manifest: manifest)
  precondition(try! PinnedModelDownload.installedModel(in: sentinel, modelID: "fixture", manifest: manifest))
  let weights = installed.appendingPathComponent("config.json")
  chmod(weights.path, 0o600); try Data("xyz".utf8).write(to: weights)
  do { _ = try PinnedModelDownload.installedModel(in: sentinel, modelID: "fixture", manifest: manifest); preconditionFailure("corrupt accepted") }
  catch ModelTreeVerifier.Failure.hashMismatch {}
  let capacityParent = parent.appendingPathComponent("capacity")
  try fm.createDirectory(at: capacityParent, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  do {
   _ = try await PinnedModelDownload.download(mirror: mirror, parent: capacityParent, manifest: manifest, additionalBytes: 1 << 60, transport: http)
   preconditionFailure("space guard bypassed")
  } catch ModelTreeVerifier.Failure.insufficientSpace {}
  precondition(Fixture.requests == requestsBefore && (try! fm.contentsOfDirectory(atPath: capacityParent.path).isEmpty))
  // Owner API/cancellation/completion tests, not a live GUI recreation test.
  let job = ModelComponentJob.shared, revision = ModelComponentJob.shared.completionRevision
  precondition(job.start(downloading: true, success: "done", failure: { error in precondition(error is CancellationError); return "cancelled" }) {
   try await Task.sleep(nanoseconds: 30_000_000_000); return parent
  })
  precondition(!job.start(downloading: true, success: "duplicate", failure: { _ in "failure" }) { preconditionFailure("duplicate ran") })
  job.cancel(status: "cancelling")
  precondition(job.busy && job.cancelling && job.status == "cancelling")
  for _ in 0..<200 { if !job.busy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
  precondition(!job.busy && !job.cancelling && job.status == "cancelled" && job.completionRevision == revision + 1)
  precondition(job.start(downloading: false, success: "done", failure: { _ in "failure" }) { parent })
  for _ in 0..<200 { if !job.busy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
  precondition(!job.busy && job.status == "done" && job.completionRevision == revision + 2)
  do {
   let resumeParent = parent.appendingPathComponent("resume-fixture")
   try fm.createDirectory(at: resumeParent, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   let first = ModelManifest.Entry(path: "a.json", size: 3, sha256: hash)
   let secondBytes = Data("def".utf8), secondHash = ModelManifest.hash(Data("def".utf8))
   let second = ModelManifest.Entry(path: "nested/b.bin", size: 3, sha256: secondHash)
   let canonical = "[{\"path\":\"a.json\",\"sha256\":\"\(hash)\",\"size\":3},{\"path\":\"nested/b.bin\",\"sha256\":\"\(secondHash)\",\"size\":3}]"
   let resumeManifest = try ModelManifest(files: [first, second], expectedDigest: ModelManifest.hash(Data(canonical.utf8)))
   var cache: VerifiedModelDownloadCache? = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest)
   let identity = try VerifiedModelDownloadCache.identifier(mirror: mirror, manifest: resumeManifest)
   precondition(identity == (try! VerifiedModelDownloadCache.identifier(mirror: mirror, manifest: resumeManifest)))
   precondition(identity != (try! VerifiedModelDownloadCache.identifier(mirror: URL(string:"https://other.invalid/pinned")!, manifest: resumeManifest)))
   do { _ = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest); preconditionFailure("concurrent checkpoint lease") } catch VerifiedModelDownloadCache.Failure.busy {}
   var requests: [String] = []
   do {
    _ = try await PinnedModelDownload.download(mirror: mirror, parent: resumeParent, manifest: resumeManifest, checkpoint: cache) { url, _, emit in
     requests.append(url.lastPathComponent)
     if url.lastPathComponent == "a.json" { try emit(data) }
     else { try emit(Data("d".utf8)); throw CancellationError() }
    }
    preconditionFailure("interrupted model accepted")
   } catch is CancellationError {}
   precondition(requests == ["a.json", "b.bin"] && (try! cache!.contains(first)) && !(try! cache!.contains(second)))
   cache = nil // Simulated process/session recreation, not system background transfer proof.
   cache = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest)
   requests = []
   let resumeProgress = ProgressCapture()
   let resumed = try await PinnedModelDownload.download(mirror: mirror, parent: resumeParent, manifest: resumeManifest, checkpoint: cache, progress: { resumeProgress.append($0) }) { url, _, emit in
    requests.append(url.lastPathComponent); try emit(secondBytes)
   }
   precondition(requests == ["b.bin"])
   precondition(resumeProgress.snapshots().last == .init(receivedBytes: 3, reusedBytes: 3, verifiedBytes: 6, totalBytes: 6))
   precondition(resumeProgress.snapshots().allSatisfy { $0.valid })
   try ModelTreeVerifier.verify(root: resumed, manifest: resumeManifest)
   try fm.removeItem(at: resumed)
   requests = []
   let allReusedProgress = ProgressCapture()
   let reused = try await PinnedModelDownload.download(mirror: mirror, parent: resumeParent, manifest: resumeManifest, checkpoint: cache, progress: { allReusedProgress.append($0) }) { _, _, _ in preconditionFailure("verified files redownloaded") }
   precondition(allReusedProgress.snapshots().last == .init(receivedBytes: 0, reusedBytes: 6, verifiedBytes: 6, totalBytes: 6))
   try ModelTreeVerifier.verify(root: reused, manifest: resumeManifest)
   try fm.removeItem(at: reused)
   let preserved = cache!.root.appendingPathComponent(hash)
   try Data("xyz".utf8).write(to: preserved)
   do { _ = try cache!.contains(first); preconditionFailure("corrupt checkpoint accepted") } catch ModelTreeVerifier.Failure.hashMismatch {}
   precondition(try! Data(contentsOf: preserved) == Data("xyz".utf8))
   precondition(!(try! cache!.reusableOrQuarantine(first)))
   let container = cache!.root.deletingLastPathComponent()
   let quarantined = try fm.contentsOfDirectory(atPath: container.path).filter { $0.hasPrefix(".quarantine-") }
   precondition(quarantined.count == 1)
   precondition(try! Data(contentsOf: container.appendingPathComponent(quarantined[0])) == Data("xyz".utf8))
   let recovered = try await PinnedModelDownload.download(mirror: mirror, parent: resumeParent, manifest: resumeManifest, checkpoint: cache) { url, _, emit in
    precondition(url.lastPathComponent == "a.json")
    try emit(data)
   }
   try ModelTreeVerifier.verify(root: recovered, manifest: resumeManifest)
   try fm.removeItem(at: recovered)
   try Data("invalid length".utf8).write(to: preserved)
   precondition(!(try! cache!.reusableOrQuarantine(first)))
   let sizeRecovery = try await PinnedModelDownload.download(mirror: mirror, parent: resumeParent, manifest: resumeManifest, checkpoint: cache) { url, _, emit in
    precondition(url.lastPathComponent == "a.json")
    try emit(data)
   }
   try ModelTreeVerifier.verify(root: sizeRecovery, manifest: resumeManifest)
   try fm.removeItem(at: sizeRecovery)
   precondition(chmod(preserved.path, 0o640) == 0)
   do { _ = try cache!.contains(first); preconditionFailure("group-readable cache accepted") } catch VerifiedModelDownloadCache.Failure.unavailable {}
   do { _ = try cache!.reusableOrQuarantine(first); preconditionFailure("unsafe cache quarantined") } catch VerifiedModelDownloadCache.Failure.unavailable {}
   precondition(fm.fileExists(atPath: preserved.path))
   precondition(chmod(preserved.path, 0o600) == 0)
   for index in 1...2 {
    let name = container.appendingPathComponent(".quarantine-fixture-\(index)")
    precondition(fm.createFile(atPath: name.path, contents: Data(), attributes: [.posixPermissions: 0o600]))
   }
   try Data("too long".utf8).write(to: preserved)
   do { _ = try cache!.reusableOrQuarantine(first); preconditionFailure("quarantine cap ignored") } catch VerifiedModelDownloadCache.Failure.capacity {}
   precondition(try! Data(contentsOf: preserved) == Data("too long".utf8))
   try data.write(to: preserved)
   try Data("preserve corrupt known bytes".utf8).write(to: preserved)
   let leftover = cache!.root.appendingPathComponent(".partial-crash-fixture")
   precondition(fm.createFile(atPath: leftover.path, contents: Data("preserve crash bytes".utf8), attributes: [.posixPermissions: 0o600]))
   try cache!.discardVerified(manifest: resumeManifest)
   precondition(!fm.fileExists(atPath: cache!.root.path))
   let archives = try fm.contentsOfDirectory(atPath: container.path).filter { $0.hasPrefix(".retained-job-") }
   precondition(archives.count == 1)
   precondition(try! Data(contentsOf: container.appendingPathComponent(archives[0]).appendingPathComponent(hash)) == Data("preserve corrupt known bytes".utf8))
   do { _ = try cache!.contains(first); preconditionFailure("retired lease reused") } catch VerifiedModelDownloadCache.Failure.unavailable {}
   precondition(try! Data(contentsOf: container.appendingPathComponent(archives[0]).appendingPathComponent(leftover.lastPathComponent)) == Data("preserve crash bytes".utf8))
   cache = nil // Releasing the old lease is required before reopening the same job.
   do {
    let fresh = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest)
    precondition(!(try! fresh.contains(first)))
    let freshCrash = fresh.root.appendingPathComponent(".partial-second-fixture")
    precondition(fm.createFile(atPath: freshCrash.path, contents: Data("retained".utf8), attributes: [.posixPermissions: 0o600]))
    var ownedArchives: [URL] = []
    for index in 1...3 {
     let placeholder = container.appendingPathComponent(".retained-job-fixture-\(index)")
     try fm.createDirectory(at: placeholder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
     ownedArchives.append(placeholder)
    }
    do { try fresh.discardVerified(manifest: resumeManifest); preconditionFailure("retained job cap ignored") } catch VerifiedModelDownloadCache.Failure.capacity {}
    precondition(try! Data(contentsOf: freshCrash) == Data("retained".utf8))
    for placeholder in ownedArchives { try fm.removeItem(at: placeholder) } // Owned test fixtures only.
    try fresh.discardVerified(manifest: resumeManifest)
   }
   cache = nil
   do {
    let held = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest)
    do { try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: resumeParent, manifest: resumeManifest); preconditionFailure("busy cleanup accepted") } catch VerifiedModelDownloadCache.Failure.busy {}
    withExtendedLifetime(held) {}
   }
   try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: resumeParent, manifest: resumeManifest)
   let copySource = resumeParent.appendingPathComponent("copy-source")
   try fm.createDirectory(at: copySource, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   precondition(fm.createFile(atPath: copySource.appendingPathComponent("a.json").path, contents: data, attributes: [.posixPermissions: 0o600]))
   let failedCopy = try VerifiedModelDownloadCache(parent: resumeParent, mirror: mirror, manifest: resumeManifest, copyFile: { _, destination in
    try Data("a".utf8).write(to: destination); throw VerifiedModelDownloadCache.Failure.unavailable
   })
   do { try failedCopy.retain(first, from: copySource); preconditionFailure("failed copy retained") } catch VerifiedModelDownloadCache.Failure.unavailable {}
   precondition(try! fm.contentsOfDirectory(atPath: failedCopy.root.path).isEmpty)
   try failedCopy.discardVerified(manifest: resumeManifest)
   var admitted: [VerifiedModelDownloadCache] = []
   for index in 0..<4 {
    admitted.append(try VerifiedModelDownloadCache(parent: resumeParent, mirror: URL(string: "https://fixture-\(index).invalid/pinned")!, manifest: resumeManifest))
   }
   do { _ = try VerifiedModelDownloadCache(parent: resumeParent, mirror: URL(string: "https://fifth.invalid/pinned")!, manifest: resumeManifest); preconditionFailure("checkpoint job cap ignored") } catch VerifiedModelDownloadCache.Failure.capacity {}
   let beforeAbsentCleanup = try fm.contentsOfDirectory(atPath: container.path).sorted()
   try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: URL(string: "https://fifth.invalid/pinned")!, parent: resumeParent, manifest: resumeManifest)
   precondition(try! fm.contentsOfDirectory(atPath: container.path).sorted() == beforeAbsentCleanup)
   for checkpoint in admitted { try checkpoint.discardVerified(manifest: resumeManifest) }
   admitted = []
   let emptyCleanupParent = resumeParent.appendingPathComponent("empty-cleanup-parent")
   try fm.createDirectory(at: emptyCleanupParent, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: emptyCleanupParent, manifest: resumeManifest)
   precondition(try! fm.contentsOfDirectory(atPath: emptyCleanupParent.path).isEmpty)
   let overflowing = emptyCleanupParent.appendingPathComponent("VoxAgentModelDownloadCheckpoints")
   try fm.createDirectory(at: overflowing, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   for index in 0..<129 { precondition(fm.createFile(atPath: overflowing.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600])) }
   try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: emptyCleanupParent, manifest: resumeManifest)
   precondition(try! fm.contentsOfDirectory(atPath: overflowing.path).count == 129)
   let boundParent = resumeParent.appendingPathComponent("bound-cleanup-parent")
   try fm.createDirectory(at: boundParent, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   do { let initial = try VerifiedModelDownloadCache(parent: boundParent, mirror: mirror, manifest: resumeManifest); try initial.discardVerified(manifest: resumeManifest) }
   let boundContainer = boundParent.appendingPathComponent("VoxAgentModelDownloadCheckpoints")
   let initialCount = try fm.contentsOfDirectory(atPath: boundContainer.path).count
   for index in initialCount..<128 { precondition(fm.createFile(atPath: boundContainer.appendingPathComponent("foreign-\(index)").path, contents: Data(), attributes: [.posixPermissions: 0o600])) }
   do { _ = try VerifiedModelDownloadCache(parent: boundParent, mirror: mirror, manifest: resumeManifest); preconditionFailure("129th directory admitted") } catch VerifiedModelDownloadCache.Failure.capacity {}
   try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: boundParent, manifest: resumeManifest)
   precondition(try! fm.contentsOfDirectory(atPath: boundContainer.path).count == 128)
   let boundJob = ".qwen-checkpoint-" + (try VerifiedModelDownloadCache.identifier(mirror: mirror, manifest: resumeManifest))
   try fm.createDirectory(at: boundContainer.appendingPathComponent(boundJob), withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   let missingLock = boundContainer.appendingPathComponent(boundJob + ".lock")
   try fm.removeItem(at: missingLock) // Owned synthetic lock; tests cleanup cannot recreate it.
   do { try await PinnedModelDownload.cleanupVerifiedCheckpoint(mirror: mirror, parent: boundParent, manifest: resumeManifest); preconditionFailure("missing lock recreated") } catch VerifiedModelDownloadCache.Failure.unavailable {}
   precondition(!fm.fileExists(atPath: missingLock.path))
   precondition(fm.fileExists(atPath: boundContainer.appendingPathComponent(boundJob).path))
   try fm.removeItem(at: resumeParent) // Owned synthetic fixture only; production cache is never deleted here.
   print("PASS: stable checkpoint identity, exclusive job lease, interrupted-file restart, rehashed complete-file reuse, no download for all verified files, corrupt cache refusal")
  }
  do {
   let old = ProgressCapture(), expected = PinnedModelDownload.Progress(receivedBytes: 3, reusedBytes: 0, verifiedBytes: 0, totalBytes: 6)
   precondition(job.startDownload(success: "progress done", failure: { _ in "failure" }) { report in
    old.keep(report); report(expected)
    report(.init(receivedBytes: -1, reusedBytes: 0, verifiedBytes: 0, totalBytes: 6))
    report(.init(receivedBytes: 1, reusedBytes: 0, verifiedBytes: 0, totalBytes: 6))
    try await Task.sleep(nanoseconds: 150_000_000); return parent
   })
   for _ in 0..<200 { if job.downloadProgress == expected { break }; try await Task.sleep(nanoseconds: 1_000_000) }
   precondition(job.downloadProgress == expected)
   try await Task.sleep(nanoseconds: 30_000_000)
   precondition(job.downloadProgress == expected) // Give rejected reports time to drain.
   for _ in 0..<200 { if !job.busy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
   precondition(!job.busy && job.downloadProgress == nil)
   precondition(job.startDownload(success: "next done", failure: { _ in "failure" }) { report in
    report(.init(receivedBytes: 0, reusedBytes: 3, verifiedBytes: 0, totalBytes: 6))
    try await Task.sleep(nanoseconds: 150_000_000); return parent
   })
   for _ in 0..<200 { if job.downloadProgress != nil { break }; try await Task.sleep(nanoseconds: 1_000_000) }
   precondition(job.downloadProgress == .init(receivedBytes: 0, reusedBytes: 3, verifiedBytes: 0, totalBytes: 6))
   old.sendOld(.init(receivedBytes: 3, reusedBytes: 3, verifiedBytes: 3, totalBytes: 6)) // Dominates new counters: only old token can reject it.
   try await Task.sleep(nanoseconds: 30_000_000)
   precondition(job.downloadProgress == .init(receivedBytes: 0, reusedBytes: 3, verifiedBytes: 0, totalBytes: 6))
   for _ in 0..<200 { if !job.busy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
   precondition(!job.busy)
   precondition(job.downloadProgress == nil)
   old.sendOld(.init(receivedBytes: 3, reusedBytes: 3, verifiedBytes: 3, totalBytes: 6))
   try await Task.sleep(nanoseconds: 10_000_000)
   precondition(job.downloadProgress == nil)
   precondition(!PinnedModelDownload.Progress(receivedBytes: 4, reusedBytes: 3, verifiedBytes: 0, totalBytes: 6).valid)
   precondition(!PinnedModelDownload.Progress(receivedBytes: 1, reusedBytes: 1, verifiedBytes: 3, totalBytes: 6).valid)
   precondition(job.startDownload(success: "coalesced", failure: { _ in "failure" }) { report in
    for count in 0...1000 { report(.init(receivedBytes: Int64(count), reusedBytes: 0, verifiedBytes: 0, totalBytes: 1000)) }
    try await Task.sleep(nanoseconds: 150_000_000); return parent
   })
   for _ in 0..<200 { if job.downloadProgress?.receivedBytes == 1000 { break }; try await Task.sleep(nanoseconds: 1_000_000) }
   precondition(job.downloadProgress == .init(receivedBytes: 1000, reusedBytes: 0, verifiedBytes: 0, totalBytes: 1000))
   for _ in 0..<200 { if !job.busy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
   precondition(!job.busy && job.downloadProgress == nil)
   print("PASS: received/reused/verified progress totals, invalid and regressive snapshot rejection, stale previous-job callback rejection")
  }
  print("PASS: synthetic HTTP success/status/encoding/oversize/truncation/hash; bounded transport; cancellation cleanup; mirror restrictions. No real network.")
 }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vox-download-test-') as tmp:
        root = Path(tmp).resolve()
        parent = root / 'private-parent'
        parent.mkdir(mode=0o700)
        harness = root / 'check.swift'
        harness.write_text(HARNESS)
        files = ['ModelManifest', 'ModelTreeVerifier', 'ModelStore', 'ASRComponentResolver', 'QwenModelCatalog', 'PinnedModelDownload', 'VerifiedModelDownloadCache', 'OfflineModelImporter', 'ModelComponentJob']
        subprocess.run(['swiftc', '-sdk', args.sdk, '-swift-version', '5', '-parse-as-library',
                        *[str(repo / f'macos/VoxLog/Services/{name}.swift') for name in files],
                        str(harness), '-o', str(root / 'check')], check=True, timeout=90)
        subprocess.run([str(root / 'check'), str(parent)], check=True, timeout=30)
        crash_parent = root / 'crash-parent'
        crash_parent.mkdir(mode=0o700)
        command = [str(root / 'check'), str(crash_parent)]
        holder = subprocess.Popen(command + ['hold'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([holder.stdout], [], [], 10)
            if not ready or holder.stdout.readline().strip() != 'READY':
                raise AssertionError('checkpoint holder did not become ready')
            busy = subprocess.run(command + ['busy'], check=True, capture_output=True,
                                  text=True, timeout=10)
            assert busy.stdout.strip() == 'BUSY'
            holder.send_signal(signal.SIGKILL)
            holder.communicate(timeout=10)
            assert holder.returncode == -signal.SIGKILL
            reopened = subprocess.run(command + ['reopen'], check=True, capture_output=True,
                                      text=True, timeout=10)
            assert reopened.stdout.strip() == 'REOPENED_VERIFIED'
            print('PASS: independent-process lease exclusion, SIGKILL lease release, '
                  'complete synthetic checkpoint rehash after process restart')
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.communicate(timeout=10)



if __name__ == '__main__':
    main()
