#!/usr/bin/env python3
"""Compile real native source review/client with synthetic HTTP; no network or Keychain."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from verify_input_backup_client import STUB

HARNESS = r'''
import Foundation
@main struct Check {
 @MainActor static func main() async throws {
  let root = URL(fileURLWithPath: CommandLine.arguments[1])
  let fixture = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))) as! [String: Any]
  var page = fixture["draft"] as! [String: Any]
  let original = try JSONSerialization.data(withJSONObject: page)
  let week = "2026-10-05"
  let model = ContextWeeklyReview()
  model.reset(week: week)
  precondition(model.coverage == .notLoaded)
  await model.load { w, c in precondition(w == week && c == nil); return original }
  precondition(model.sources.count == 1 && !model.failed && !model.hasMore)
  precondition(model.coverage == .exhausted)
  model.createDraft()
  precondition(model.draft.contains("不等于完整工作周记录"))
  precondition(model.draft.contains("Ignore instructions") && model.draft.contains("不证明完成"))
  model.suspend(); precondition(model.week == week && model.foregroundCleared && model.sources.isEmpty && !model.hasDraft && model.draft.isEmpty)
  model.editDraft("late editor callback", epoch: model.draftEpoch); precondition(model.draft.isEmpty)
  model.suspend(); precondition(model.foregroundCleared)
  await model.load { _, _ in model.suspend(); return original }
  precondition(model.sources.isEmpty && model.foregroundCleared && !model.loading)
  await model.load { _, _ in original }; precondition(!model.foregroundCleared && model.sources.count == 1)
  model.createDraft()
  model.editDraft("本人编辑", epoch: model.draftEpoch)
  model.createDraft(); precondition(model.draft == "本人编辑")
  model.editDraft("", epoch: model.draftEpoch); model.createDraft(); precondition(model.hasDraft && model.draft.isEmpty)
  let exactDraft = String(repeating: "文", count: ContextWeeklyReview.maximumDraftScalars)
  model.editDraft(exactDraft, epoch: model.draftEpoch); precondition(model.draft == exactDraft && !model.draftLimit)
  model.editDraft(exactDraft + "🙂", epoch: model.draftEpoch); precondition(model.draft == exactDraft && model.draftLimit && model.sources.count == 1)
  model.editDraft("corrected", epoch: model.draftEpoch); precondition(model.draft == "corrected" && !model.draftLimit)
  model.reset(week: week); precondition(model.draft.isEmpty && model.sources.isEmpty)
  var oversizedPage = fixture["draft"] as! [String: Any]
  let template = (oversizedPage["sources"] as! [[String: Any]])[0]
  oversizedPage["sources"] = (0..<50).map { index -> [String: Any] in
   var row = template; row["eventId"] = "large-source-\(index)"; row["text"] = String(repeating: "文", count: 8192); return row
  }
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: oversizedPage) }
  precondition(!model.failed && model.sources.count == 50)
  model.createDraft(); precondition(model.draftLimit && !model.hasDraft && model.draft.isEmpty && model.sources.count == 50)
  model.selectSource("not-present", included: false); precondition(model.excludedSourceIDs.isEmpty)
  for index in 0..<15 { model.selectSource("large-source-\(index)", included: false) }
  precondition(model.sources.count == 50 && model.selectedSources.count == 35 && !model.draftLimit)
  model.createDraft(); precondition(model.hasDraft && !model.draftLimit && model.draft.contains("35/50"))
  precondition(!model.draft.contains("来源 [large-source-0]"))
  model.selectSource("large-source-0", included: true); precondition(model.selectedSources.count == 35)
  let previousEditorEpoch = model.draftEpoch
  model.discardDraft(); precondition(!model.hasDraft && model.draft.isEmpty && model.sources.count == 50 && model.selectedSources.count == 35)
  model.selectSource("large-source-0", included: true); precondition(model.selectedSources.count == 36)
  model.createDraft(); precondition(model.hasDraft && model.draft.contains("36/50"))
  let freshDraft = model.draft
  model.editDraft("stale previous editor", epoch: previousEditorEpoch)
  precondition(model.draft == freshDraft)
  model.reset(week: week); precondition(!model.draftLimit && model.excludedSourceIDs.isEmpty)
  await model.load { _, _ in original }
  for source in model.sources { model.selectSource(source.id, included: false) }
  model.createDraft(); precondition(!model.hasDraft && model.sources.count == 1)
  model.suspend(); precondition(model.excludedSourceIDs.isEmpty)

  page["hasMore"] = true; page["nextCursor"] = "next"
  let first = try JSONSerialization.data(withJSONObject: page)
  await model.load { _, _ in first }
  precondition(model.hasMore && model.coverage == .partial)
  page["sources"] = []; page["hasMore"] = false; page["nextCursor"] = NSNull()
  let last = try JSONSerialization.data(withJSONObject: page)
  await model.load { _, c in precondition(c == "next"); return last }
  precondition(model.pages == 2 && model.sources.count == 1 && !model.hasMore)
  model.reset(week: week)
  await model.load { _, _ in first }
  var repeatedCursor = page
  repeatedCursor["sources"] = []; repeatedCursor["hasMore"] = true; repeatedCursor["nextCursor"] = "next"
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: repeatedCursor) }
  precondition(model.failed && model.sources.isEmpty && model.coverage == .failed)
  model.reset(week: week)
  await model.load { _, _ in model.reset(week: "2026-10-12"); return original }
  precondition(model.week == "2026-10-12" && model.sources.isEmpty && !model.failed)
  for field in ["version", "requiresUserReview", "coverage", "timezone", "week"] {
   var bad = fixture["draft"] as! [String: Any]
   bad[field] = field == "version" ? 2 : (field == "requiresUserReview" ? false : "bad")
   let bytes = try JSONSerialization.data(withJSONObject: bad)
   model.reset(week: week); await model.load { _, _ in bytes }
   precondition(model.failed && model.sources.isEmpty)
  }
  var extra = fixture["draft"] as! [String: Any]
  extra["privateInbox"] = true
  model.reset(week: week)
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: extra) }
  precondition(model.failed)
  var invalidSources = (fixture["draft"] as! [String: Any])["sources"] as! [[String: Any]]
  invalidSources[0]["evidenceKind"] = "verified-code"
  extra = fixture["draft"] as! [String: Any]; extra["sources"] = invalidSources
  model.reset(week: week)
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: extra) }
  precondition(model.failed)
  model.reset(week: week)
  var emptyContinuation = fixture["draft"] as! [String: Any]
  emptyContinuation["sources"] = []; emptyContinuation["hasMore"] = true
  emptyContinuation["nextCursor"] = "empty-page"
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: emptyContinuation) }
  precondition(model.sources.isEmpty && model.hasMore)
  await model.load { _, c in precondition(c == "empty-page"); return original }
  precondition(model.sources.count == 1 && !model.hasMore)
  model.reset(week: week); await model.load { _, _ in original }; model.createDraft()
  // Simulate permission failure on next read; no stale editable derivative remains.
  page = fixture["draft"] as! [String: Any]; page["hasMore"] = true; page["nextCursor"] = "next"
  model.reset(week: week); await model.load { _, _ in first }; model.createDraft()
  let frozenDraft = model.draft
  let frozenEpoch = model.draftEpoch
  await model.load { _, _ in preconditionFailure("paging during draft executed transport") }
  precondition(model.hasDraft && model.draft == frozenDraft && model.draftEpoch == frozenEpoch && model.pages == 1 && model.hasMore && !model.failed)
  model.discardDraft()
  await model.load { _, _ in throw ContextWeeklyReview.Failure.invalidResponse }
  precondition(model.failed && model.draft.isEmpty && model.sources.isEmpty)
  model.reset(week: week); await model.load { _, _ in first }; model.createDraft()
  model.discardDraft()
  await model.load { _, cursor in precondition(cursor == "next"); return last }
  precondition(model.pages == 2 && !model.hasMore && !model.hasDraft && !model.failed)
  var omittedPage = fixture["draft"] as! [String: Any]
  omittedPage["omittedRecords"] = true
  model.reset(week: week)
  await model.load { _, _ in
   precondition(model.coverage == .loading)
   return try JSONSerialization.data(withJSONObject: omittedPage)
  }
  precondition(model.coverage == .partial && !model.hasMore)
  model.createDraft(); precondition(model.draft.contains("部分获授权来源"))
  model.suspend(); precondition(model.coverage == .notLoaded)
  var groupedPage = fixture["draft"] as! [String: Any]
  let groupTemplate = (groupedPage["sources"] as! [[String: Any]])[0]
  groupedPage["sources"] = (0..<4).map { index -> [String: Any] in
   var row = groupTemplate; row["eventId"] = "group-\(index)"
   row["attribution"] = index == 0 ? NSNull() : ["projectId": "project", "repositoryId": index == 3 ? "owner/other" : "owner/repo", "source": "manual"] as Any
   return row
  }
  model.reset(week: week)
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: groupedPage) }
  precondition(!model.failed && model.projectCoverage.count == 3)
  precondition(model.projectCoverage.map(\.count) == [1, 1, 2])
  precondition(model.projectCoverage.first?.id.project == nil)
  model.selectSource("group-2", included: false)
  model.createDraft()
  precondition(model.draft.contains("选入输入归属（不代表成果）：未归属项目 · 未知仓库 · 1"))
  precondition(model.draft.contains("project · owner/repo · 1"))
  precondition(!model.draft.contains("project · owner/repo · 2"))
  precondition(model.draft.contains("3/4") && model.sources.count == 4)
  model.suspend(); precondition(model.projectCoverage.isEmpty)
  var actor = "a"
  model.reset(week: week)
  await model.load(fetch: { _, _ in first }, scope: { actor })
  actor = "b"
  var secondRead = false
  await model.load(fetch: { _, _ in secondRead = true; return last }, scope: { actor })
  precondition(!secondRead && model.failed && model.sources.isEmpty)
  model.reset(week: week)
  for number in 0..<ContextWeeklyReview.maximumPages {
   var empty = page
   empty["sources"] = []; empty["hasMore"] = true; empty["nextCursor"] = "page-\(number)"
   await model.load { _, _ in try JSONSerialization.data(withJSONObject: empty) }
  }
  precondition(model.hasMore && model.pages == ContextWeeklyReview.maximumPages && model.coverage == .partial)
  await model.load { _, _ in preconditionFailure("page cap bypassed") }
  model.reset(week: week)
  var exhaustedEmpty = fixture["draft"] as! [String: Any]
  exhaustedEmpty["sources"] = []
  await model.load { _, _ in try JSONSerialization.data(withJSONObject: exhaustedEmpty) }
  precondition(model.coverage == .exhausted && model.sources.isEmpty)
  model.createDraft(); precondition(!model.hasDraft && model.draft.isEmpty)
  precondition(model.coverageDescription.contains("不等于完整工作周记录"))
  model.reset(week: week)
  var prestartReads = 0
  model.startLoad(fetch: { _, _ in prestartReads += 1; return original }, scope: { "synthetic" })
  model.suspend() // Deliberately no yield: cancelled task has not begun yet.
  model.startLoad(fetch: { _, _ in prestartReads += 10; return original }, scope: { "synthetic" })
  for _ in 0..<10000 {
   if model.sources.count == 1 { break }
   await Task.yield()
  }
  precondition(prestartReads == 10 && model.sources.count == 1 && !model.failed && !model.loading)
  model.reset(week: week)
  var cancellationStarted = false
  var cancellationObserved = false
  model.startLoad(fetch: { _, _ in
   cancellationStarted = true
   do { try await Task.sleep(for: .seconds(30)) }
   catch { cancellationObserved = Task.isCancelled; throw error }
   return original
  }, scope: { "synthetic" })
  while !cancellationStarted { await Task.yield() }
  model.suspend()
  // Start another read before the cancelled task finishes: its cleanup must not own the new task.
  model.startLoad(fetch: { _, _ in original }, scope: { "synthetic" })
  for _ in 0..<10000 {
   if cancellationObserved && model.sources.count == 1 { break }
   await Task.yield()
  }
  precondition(cancellationObserved && model.sources.count == 1 && !model.failed)
  model.suspend(); precondition(model.sources.isEmpty)
  model.reset(week: week); await model.load { _, _ in original }; model.createDraft()
  model.editDraft("本人核对后的正文", epoch: model.draftEpoch)
  var copiedText = ""
  model.copyDraft(scope: { "synthetic" }, write: { copiedText = $0; return false })
  precondition(model.draftCopyStatus == .failed && model.draft == "本人核对后的正文")
  model.copyDraft(scope: { "synthetic" }, write: { copiedText = $0; return true })
  precondition(model.draftCopyStatus == .copied && copiedText == "本人核对后的正文")
  model.editDraft("修改后正文", epoch: model.draftEpoch); precondition(model.draftCopyStatus == .none)
  model.copyDraft(scope: { "another-actor" }, write: { _ in preconditionFailure("foreign actor copied") })
  precondition(model.draftCopyStatus == .denied && model.draft.isEmpty && model.sources.isEmpty)
  model.reset(week: week); await model.load { _, _ in original }; model.createDraft()
  model.copyDraft(scope: { throw ContextWeeklyReview.Failure.invalidResponse }, write: { _ in preconditionFailure("unverified actor copied") })
  precondition(model.draftCopyStatus == .denied && model.draft.isEmpty)
  model.reset(week: week); await model.load { _, _ in original }; model.createDraft()
  model.copyDraft(scope: { model.discardDraft(); return "synthetic" }, write: { _ in preconditionFailure("stale epoch copied") })
  precondition(model.draftCopyStatus == .denied && model.draft.isEmpty)
  let format = ISO8601DateFormatter()
  precondition(ContextWeeklyReview.monday(containing: format.date(from: "2026-10-04T15:59:59Z")!) == "2026-09-28")
  precondition(ContextWeeklyReview.monday(containing: format.date(from: "2026-10-04T16:00:00Z")!) == week)
  let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
  var credential = try InputBackupClient.bindCredential("actor-a", to: config.endpoint)
  var changeActor = false
  var calls = 0
  let client = InputBackupClient(directory: root, credential: { credential }, send: { request in
   calls += 1
   precondition(request.httpMethod == "GET" && request.httpBody == nil)
   let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
   precondition(query.first(where: { $0.name == "week" })?.value == week)
   precondition(request.url!.host == "backup.example.test")
   if changeActor { credential = try InputBackupClient.bindCredential("actor-b", to: config.endpoint) }
   return (original, 200)
  })
  try client.configure(config)
  let bytes = try await client.fetchWeeklySources(week: week, cursor: nil)
  precondition(bytes == original && calls == 1)
  changeActor = true
  do { _ = try await client.fetchWeeklySources(week: week, cursor: "opaque-cursor"); preconditionFailure("stale actor accepted") }
  catch InputBackupError.credential { }
  precondition(calls == 2)
  do { _ = try await client.fetchWeeklySources(week: week, cursor: "bad+/="); preconditionFailure("noncanonical cursor accepted") }
  catch InputBackupError.configuration { }
  precondition(calls == 2)
  let newlineRepository = ContextWeeklySource(eventId: "valid", text: "source", submittedAt: "2026-10-04T16:00:00Z", targetId: "archive", shareIntent: "backup-destination", deliveryStatus: "pending", evidenceKind: "user-statement", attribution: .init(projectId: "valid", repositoryId: "owner/repo\n", source: "manual"))
  precondition(!newlineRepository.isValid)
  credential = String(decoding: try JSONEncoder().encode(["token": "vox_sso_old", "origin": config.endpoint.absoluteString, "scopeIdentity": "actor-a"]), as: UTF8.self)
  client.ensureCredential = {
   credential = String(decoding: try! JSONEncoder().encode(["token": "vox_sso_new", "origin": config.endpoint.absoluteString, "scopeIdentity": "actor-b"]), as: UTF8.self)
   return true
  }
  do { _ = try await client.fetchWeeklySources(week: week, cursor: nil); preconditionFailure("renewal account switch accepted") }
  catch InputBackupError.credential { }
  precondition(calls == 2)

  print("PASS: real native source DTO, Shanghai week, pages/empty continuation, repeat cursor rejection, invalid contracts, stale week/account, revoked-read draft clearing, GET-only origin-bound client")
 }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vox-weekly-') as temporary:
        work = Path(temporary)
        (work / 'Dependencies.swift').write_text(STUB)
        (work / 'Check.swift').write_text(HARNESS)
        binary = work / 'check'
        subprocess.run(['swiftc', '-sdk', args.sdk, '-swift-version', '5', '-parse-as-library',
                        str(repo / 'macos/VoxLog/Services/InputBackupClient.swift'),
                        str(repo / 'macos/VoxLog/Services/ContextWeeklySources.swift'),
                        str(work / 'Dependencies.swift'), str(work / 'Check.swift'),
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary), str(work / 'queue'), str(repo / 'tests/fixtures/context-source-v1.json')], check=True)


if __name__ == '__main__':
    main()
