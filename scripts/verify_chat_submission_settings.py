#!/usr/bin/env python3
"""CLT-compatible checks of the actual Swift send-mode preference policy."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="vox-send-mode-") as temp:
    directory = Path(temp)
    harness = directory / "main.swift"
    harness.write_text(r'''
import Foundation
let defaults = UserDefaults(suiteName: "vox-mode-test-" + UUID().uuidString)!
let root = URL(fileURLWithPath: CommandLine.arguments[1])
precondition(try ChatSubmissionSettings.repository(defaults: defaults) == nil)
defaults.set(true, forKey: ChatSubmissionSettings.executionKey)
do {
    _ = try ChatSubmissionSettings.repository(defaults: defaults)
    fatalError("execution without repository accepted")
} catch {}
let repo = root.appendingPathComponent("repo with spaces")
try FileManager.default.createDirectory(at: repo, withIntermediateDirectories: true)
try "gitdir: /unused/worktree".write(to: repo.appendingPathComponent(".git"), atomically: true, encoding: .utf8)
defaults.set(repo.path, forKey: ChatSubmissionSettings.repositoryKey)
let chosen = try ChatSubmissionSettings.repository(defaults: defaults)
precondition(chosen == repo.resolvingSymlinksInPath().path)
try FileManager.default.removeItem(at: repo)
do {
    _ = try ChatSubmissionSettings.repository(defaults: defaults)
    fatalError("deleted repository accepted")
} catch {}
defaults.set(false, forKey: ChatSubmissionSettings.executionKey)
let recording = try ChatSubmissionSettings.repository(defaults: defaults)
precondition(recording == nil)
print("PASS: default record-only, explicit execution, missing/deleted repository, worktree path, mode switch")
'''.replace('precondition(try ChatSubmissionSettings.repository(defaults: defaults) == nil)', 'let initial = try ChatSubmissionSettings.repository(defaults: defaults)\nprecondition(initial == nil)'))
    executable = directory / "check"
    subprocess.run([
        "swiftc", "-sdk", "/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk",
        str(root / "macos/VoxLog/Services/ChatSubmissionSettings.swift"),
        str(harness), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable), str(directory)], check=True)
