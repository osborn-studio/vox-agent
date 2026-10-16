#!/usr/bin/env python3
"""Compile actual atomic offline importer with tiny isolated files, no network or user caches."""
import argparse
from pathlib import Path
import subprocess
import tempfile

HARNESS = r'''
import Foundation
import Darwin
@main struct Check {
 static func main() async throws {
  let base = URL(fileURLWithPath: CommandLine.arguments[1])
  let fm = FileManager.default
  func folder(_ name: String) throws -> URL {
   let url = base.appendingPathComponent(name)
   try fm.createDirectory(at: url, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
   return url
  }
  let support = try folder("support"), source = try folder("source")
  let emptyHash = ModelManifest.hash(Data())
  let canonical = "[{\"path\":\"config.json\",\"sha256\":\"\(emptyHash)\",\"size\":0}]"
  let manifest = try ModelManifest(files: [.init(path: "config.json", size: 0, sha256: emptyHash)], expectedDigest: ModelManifest.hash(Data(canonical.utf8)))
  let original = source.appendingPathComponent("config.json")
  try Data("wrong".utf8).write(to: original)
  func perform(_ support: URL = support) throws -> URL {
   try OfflineModelImporter.importModel(source: source, applicationSupport: support, modelID: "fixture", manifest: manifest)
  }
  func rejects(_ action: () throws -> URL) {
   do { _ = try action(); preconditionFailure("unsafe import succeeded") } catch { }
  }
  do { _ = try perform(); preconditionFailure("invalid succeeded") } catch { print("first failure:", error) }
  precondition(!fm.fileExists(atPath: support.appendingPathComponent("VoxLog/Components").path))
  precondition(try! Data(contentsOf: original) == Data("wrong".utf8))
  precondition(!((try? fm.contentsOfDirectory(atPath: support.appendingPathComponent("VoxLog").path)) ?? []).contains { $0.hasPrefix(".model-import-") })
  try Data().write(to: original)
  let mediaParent = try folder("media-parent")
  let media = try ModelTreeVerifier.stage(root: source, manifest: manifest, stagingParent: mediaParent)
  try ModelTreeVerifier.verify(root: media, manifest: manifest)
  let secondSupport = try folder("media-import-support")
  let fromMedia = try OfflineModelImporter.importModel(source: media, applicationSupport: secondSupport, modelID: "fixture", manifest: manifest)
  try ModelTreeVerifier.verify(root: fromMedia, manifest: manifest)
  precondition(fm.fileExists(atPath: media.appendingPathComponent("config.json").path))
  let installed = try perform()
  try ModelTreeVerifier.verify(root: installed, manifest: manifest)
  precondition(fm.fileExists(atPath: original.path))
  try fm.removeItem(at: original)
  precondition(try! perform() == installed) // Reuse verified cache even when source disappeared.
  let writer = open(support.appendingPathComponent("VoxLog/.offline-import.lock").path, O_RDWR)
  precondition(writer >= 0 && flock(writer, LOCK_EX | LOCK_NB) == 0)
  rejects { try perform() }
  flock(writer, LOCK_UN); close(writer)
  chmod(installed.appendingPathComponent("config.json").path, 0o600)
  try Data("tampered".utf8).write(to: installed.appendingPathComponent("config.json"))
  rejects { try perform() }
  precondition(try! Data(contentsOf: installed.appendingPathComponent("config.json")) == Data("tampered".utf8))
  let shared = try folder("shared"); chmod(shared.path, 0o777)
  rejects { try perform(shared) }
  precondition(!fm.fileExists(atPath: shared.appendingPathComponent("VoxLog").path))
  let redirected = try folder("redirected"), foreign = try folder("foreign")
  try fm.createSymbolicLink(at: redirected.appendingPathComponent("VoxLog"), withDestinationURL: foreign)
  rejects { try perform(redirected) }
  precondition(try! fm.contentsOfDirectory(atPath: foreign.path).isEmpty)
  let blocked = try folder("blocked")
  try fm.createDirectory(at: blocked.appendingPathComponent("VoxLog"), withIntermediateDirectories: false)
  let sentinel = blocked.appendingPathComponent("VoxLog/Components")
  try Data("sentinel".utf8).write(to: sentinel)
  rejects { try perform(blocked) }
  precondition(try! Data(contentsOf: sentinel) == Data("sentinel".utf8))
  let cancelled = try folder("cancelled")
  let result = await Task { () -> Bool in
   withUnsafeCurrentTask { $0?.cancel() }
   do { _ = try perform(cancelled); return false } catch is CancellationError { return true } catch { return false }
  }.value
  precondition(result && !fm.fileExists(atPath: cancelled.appendingPathComponent("VoxLog").path))
  print("PASS: failed first import does not shadow legacy; atomic valid publication/reuse; writer busy; corrupt cache retained; unsafe roots and symlinks denied; cancellation before writes")
 }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vox-offline-import-') as tmp:
        root = Path(tmp).resolve()
        harness = root / 'check.swift'
        harness.write_text(HARNESS)
        files = ['ModelManifest', 'ModelTreeVerifier', 'ModelStore', 'QwenModelCatalog',
                 'ASRComponentResolver', 'OfflineModelImporter']
        subprocess.run(['swiftc', '-sdk', args.sdk, '-swift-version', '5', '-parse-as-library',
                        *[str(repo / f'macos/VoxLog/Services/{name}.swift') for name in files],
                        str(harness), '-o', str(root / 'check')], check=True, timeout=90)
        subprocess.run([str(root / 'check'), str(root)], check=True, timeout=30)


if __name__ == '__main__':
    main()
