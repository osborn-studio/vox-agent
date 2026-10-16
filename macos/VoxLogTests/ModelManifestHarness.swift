#if MODEL_MANIFEST_HARNESS
import Foundation
import Darwin

@main enum ModelManifestHarness {
    static func main() async throws {
        let emptyHash = ModelManifest.hash(Data())
        let vector = "f54b49734a4dbbdea26e470703f398af4f5107e68dccea510e190a3ceeaa8740"
        let empty = ModelManifest.Entry(path: "config.json", size: 0, sha256: emptyHash)
        let manifest = try ModelManifest(files: [empty], expectedDigest: vector)
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--probe-store-lock" {
            do {
                _ = try ModelStore(root: URL(fileURLWithPath: CommandLine.arguments[2])).importModel(source: URL(fileURLWithPath: "/missing"), modelID: "probe", manifest: manifest)
                exit(1)
            } catch ModelStore.Failure.busy { exit(0) }
        }
        precondition(manifest.totalBytes == 0 && manifest.digest == vector)
        var checks = 1
        func rejects(_ action: () throws -> Void) {
            do { try action(); fatalError("Expected rejection") } catch { checks += 1 }
        }
        func manifestRejects(_ expected: ModelManifest.Failure, _ action: () throws -> Void) {
            do { try action(); fatalError("Expected manifest rejection") }
            catch let actual as ModelManifest.Failure { precondition(actual == expected); checks += 1 }
            catch { fatalError("Unexpected failure: \(error)") }
        }
        for path in ["../x", "/x", "a//b", "a/./b", "a.", "x y", "中文", "a\\b", ""] {
            manifestRejects(.invalidPath) { _ = try ModelManifest(files: [.init(path: path, size: 0, sha256: emptyHash)], expectedDigest: vector) }
        }
        for size: Int64 in [-1, 9 * 1024 * 1024 * 1024] {
            manifestRejects(.invalidSize) { _ = try ModelManifest(files: [.init(path: "config.json", size: size, sha256: emptyHash)], expectedDigest: vector) }
        }
        manifestRejects(.empty) { _ = try ModelManifest(files: [], expectedDigest: vector) }
        manifestRejects(.digestMismatch) { _ = try ModelManifest(files: [empty], expectedDigest: String(repeating: "0", count: 64)) }
        for paths in [["a", "A"], ["A/x", "a/y"], ["a", "a/b"]] {
            manifestRejects(.collision) { _ = try ModelManifest(files: paths.map { .init(path: $0, size: 0, sha256: emptyHash) }, expectedDigest: vector) }
        }
        manifestRejects(.invalidHash) { _ = try ModelManifest(files: [.init(path: "config.json", size: 0, sha256: emptyHash.uppercased())], expectedDigest: vector) }
        let fm = FileManager.default
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).resolvingSymlinksInPath().appendingPathComponent("build").appendingPathComponent("vox-manifest-\(UUID())")
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: root) }
        let config = root.appendingPathComponent("config.json")
        try Data().write(to: config)
        try ModelTreeVerifier.verify(root: root, manifest: manifest); checks += 1
        let emptyStage = try ModelTreeVerifier.stage(root: root, manifest: manifest, stagingParent: root)
        defer { try? fm.removeItem(at: emptyStage) }
        try ModelTreeVerifier.verify(root: emptyStage, manifest: manifest); checks += 1
        try Data("tamper".utf8).write(to: config)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: manifest) }
        try fm.removeItem(at: config)
        try fm.createSymbolicLink(at: config, withDestinationURL: root.appendingPathComponent("missing"))
        rejects { try ModelTreeVerifier.verify(root: root, manifest: manifest) }
        try fm.removeItem(at: config)
        precondition(mkfifo(config.path, 0o600) == 0)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: manifest) }
        try fm.removeItem(at: config)
        let alias = root.appendingPathComponent("alias")
        try fm.createSymbolicLink(at: alias, withDestinationURL: root)
        rejects { try ModelTreeVerifier.verify(root: alias, manifest: manifest) }
        // Same-size changes must fail SHA verification, not merely size checks.
        let payload = Data("A".utf8)
        let hash = ModelManifest.hash(payload)
        let canonical = "[{\"path\":\"config.json\",\"sha256\":\"\(hash)\",\"size\":1}]"
        let one = try ModelManifest(files: [.init(path: "config.json", size: 1, sha256: hash)], expectedDigest: ModelManifest.hash(Data(canonical.utf8)))
        try payload.write(to: config)
        try ModelTreeVerifier.verify(root: root, manifest: one); checks += 1
        try Data("B".utf8).write(to: config)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: one) }
        // Independent Python digest tests ordering, separators, nested paths and nonzero sizes.
        let samples = [("a/x", "123"), ("B", "xy"), ("a-b", "m")]
        let entries = samples.map { name, value in
            ModelManifest.Entry(path: name, size: Int64(value.utf8.count), sha256: ModelManifest.hash(Data(value.utf8)))
        }
        let multiple = try ModelManifest(files: entries, expectedDigest: CommandLine.arguments[1])
        precondition(multiple.files.map(\.path) == ["B", "a-b", "a/x"])
        precondition(multiple.totalBytes == 6)
        let nested = root.appendingPathComponent("a")
        try fm.createDirectory(at: nested, withIntermediateDirectories: false)
        for (name, value) in samples { try Data(value.utf8).write(to: root.appendingPathComponent(name)) }
        try ModelTreeVerifier.verify(root: root, manifest: multiple); checks += 1
        let nestedStage = try ModelTreeVerifier.stage(root: root, manifest: multiple, stagingParent: root)
        defer { try? fm.removeItem(at: nestedStage) }
        try ModelTreeVerifier.verify(root: nestedStage, manifest: multiple); checks += 1
        try Data("bad".utf8).write(to: nested.appendingPathComponent("x"))
        let stagesBefore = Set(try fm.contentsOfDirectory(atPath: root.path))
        do {
            _ = try ModelTreeVerifier.stage(root: root, manifest: multiple, stagingParent: root)
            fatalError("Expected late-entry hash failure")
        } catch ModelTreeVerifier.Failure.hashMismatch("a/x") { checks += 1 }
        let stagesAfter = Set(try fm.contentsOfDirectory(atPath: root.path))
        precondition(stagesAfter == stagesBefore); checks += 1
        try fm.removeItem(at: nested.appendingPathComponent("x"))
        try fm.removeItem(at: nested)
        try fm.createSymbolicLink(at: nested, withDestinationURL: root)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: multiple) }
        rejects { try ModelTreeVerifier.verify(root: alias.appendingPathComponent("a"), manifest: manifest) }
        try fm.removeItem(at: nested)
        try Data().write(to: nested)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: multiple) }
        try payload.write(to: config)
        let link = root.appendingPathComponent("hardlink")
        try fm.linkItem(at: config, to: link)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: one) }
        try fm.removeItem(at: link)
        precondition(chmod(config.path, 0o666) == 0)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: one) }
        precondition(chmod(config.path, 0o600) == 0)
        try ModelTreeVerifier.verify(root: root, manifest: one); checks += 1
        let extra = root.appendingPathComponent("ignored-secret")
        try Data("not a model".utf8).write(to: extra)
        let staged = try ModelTreeVerifier.stage(root: root, manifest: one, stagingParent: root)
        defer { try? fm.removeItem(at: staged) }
        let copied = try Data(contentsOf: staged.appendingPathComponent("config.json"))
        precondition(copied == payload)
        precondition(!fm.fileExists(atPath: staged.appendingPathComponent("ignored-secret").path))
        checks += 2
        try Data("B".utf8).write(to: config)
        try ModelTreeVerifier.verify(root: staged, manifest: one); checks += 1
        let temporary = root
        let before = Set(try fm.contentsOfDirectory(atPath: temporary.path).filter { $0.hasPrefix("vox-model-stage-") })
        rejects { _ = try ModelTreeVerifier.stage(root: root, manifest: one, stagingParent: root) }
        let after = Set(try fm.contentsOfDirectory(atPath: temporary.path).filter { $0.hasPrefix("vox-model-stage-") })
        precondition(before == after); checks += 1
        let preserved = try Data(contentsOf: config)
        precondition(preserved == Data("B".utf8)); checks += 1
        // Exercise a complete chunk plus a partial tail, including persisted file permissions.
        let large = Data(repeating: 0x41, count: 1024 * 1024 + 1)
        let largeHash = ModelManifest.hash(large)
        let largeJSON = "[{\"path\":\"config.json\",\"sha256\":\"\(largeHash)\",\"size\":1048577}]"
        let largeManifest = try ModelManifest(files: [.init(path: "config.json", size: Int64(large.count), sha256: largeHash)], expectedDigest: ModelManifest.hash(Data(largeJSON.utf8)))
        try large.write(to: config)
        let largeStage = try ModelTreeVerifier.stage(root: root, manifest: largeManifest, stagingParent: root)
        defer { try? fm.removeItem(at: largeStage) }
        let largeCopy = try Data(contentsOf: largeStage.appendingPathComponent("config.json"))
        precondition(largeCopy == large); checks += 1
        var info = stat()
        precondition(lstat(largeStage.path, &info) == 0 && info.st_mode & 0o777 == 0o700); checks += 1
        precondition(lstat(largeStage.appendingPathComponent("config.json").path, &info) == 0 && info.st_mode & 0o777 == 0o600 && info.st_nlink == 1); checks += 1
        rejects { _ = try ModelTreeVerifier.stage(root: root, manifest: largeManifest, stagingParent: alias) }
        let storeRoot = root.appendingPathComponent("store")
        try fm.createDirectory(at: storeRoot, withIntermediateDirectories: false)
        let store = ModelStore(root: storeRoot)
        let installed = try store.importModel(source: root, modelID: "test-model", manifest: largeManifest)
        try ModelTreeVerifier.verify(root: installed, manifest: largeManifest); checks += 1
        let reused = try store.importModel(source: root.appendingPathComponent("missing"), modelID: "test-model", manifest: largeManifest)
        precondition(reused == installed); checks += 1
        let receipt = storeRoot.appendingPathComponent("receipts/\(largeManifest.digest).json")
        try fm.removeItem(at: receipt)
        _ = try store.importModel(source: root.appendingPathComponent("missing"), modelID: "test-model", manifest: largeManifest)
        precondition(fm.fileExists(atPath: receipt.path)); checks += 1
        let second = try store.importModel(source: root.appendingPathComponent("missing"), modelID: "other-model", manifest: largeManifest)
        try ModelTreeVerifier.verify(root: second, manifest: largeManifest); checks += 1
        let blobNames = try fm.contentsOfDirectory(atPath: storeRoot.appendingPathComponent("blobs/sha256").path)
        precondition(blobNames == [largeHash]); checks += 1
        let stagingNames = try fm.contentsOfDirectory(atPath: storeRoot.appendingPathComponent("staging").path)
        precondition(stagingNames.isEmpty); checks += 1
        rejects { _ = try store.importModel(source: root, modelID: "../escape", manifest: largeManifest) }
        let writer = open(storeRoot.appendingPathComponent(".writer.lock").path, O_RDWR)
        precondition(writer >= 0 && flock(writer, LOCK_EX | LOCK_NB) == 0)
        do {
            _ = try store.importModel(source: root, modelID: "test-model", manifest: largeManifest)
            fatalError("Expected busy writer")
        } catch ModelStore.Failure.busy { checks += 1 }
        let contender = Process()
        contender.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        contender.arguments = ["--probe-store-lock", storeRoot.path]
        try contender.run(); contender.waitUntilExit()
        precondition(contender.terminationStatus == 0); checks += 1
        flock(writer, LOCK_UN); close(writer)
        let installedFile = installed.appendingPathComponent("config.json")
        precondition(chmod(installedFile.path, 0o600) == 0)
        try Data("bad".utf8).write(to: installedFile)
        rejects { _ = try store.importModel(source: root, modelID: "test-model", manifest: largeManifest) }
        try ModelTreeVerifier.verify(root: second, manifest: largeManifest); checks += 1
        // A corrupt shared blob cannot create a new model or replace existing projections.
        let sharedBlob = storeRoot.appendingPathComponent("blobs/sha256/\(largeHash)")
        precondition(chmod(sharedBlob.path, 0o600) == 0)
        try Data("bad".utf8).write(to: sharedBlob)
        rejects { _ = try store.importModel(source: root, modelID: "third-model", manifest: largeManifest) }
        try ModelTreeVerifier.verify(root: second, manifest: largeManifest); checks += 1
        let unsafeStore = root.appendingPathComponent("unsafe-store")
        try fm.createDirectory(at: unsafeStore, withIntermediateDirectories: false)
        try fm.createSymbolicLink(at: unsafeStore.appendingPathComponent(".writer.lock"), withDestinationURL: config)
        rejects { _ = try ModelStore(root: unsafeStore).importModel(source: root, modelID: "test", manifest: largeManifest) }
        let headroom: UInt64 = 64 * 1024 * 1024
        precondition(ModelStore.requiredImportBytes(manifest: largeManifest, cachedHashes: []) == UInt64(large.count) * 3 + headroom); checks += 1
        precondition(ModelStore.requiredImportBytes(manifest: largeManifest, cachedHashes: [largeHash]) == UInt64(large.count) + headroom); checks += 1
        precondition(ModelStore.requiredImportBytes(manifest: manifest, cachedHashes: []) == headroom); checks += 1
        let repeatedEntries = ["a", "b"].map { ModelManifest.Entry(path: $0, size: 1, sha256: hash) }
        let repeatedJSON = "[" + ["a", "b"].map { "{\"path\":\"\($0)\",\"sha256\":\"\(hash)\",\"size\":1}" }.joined(separator: ",") + "]"
        let repeated = try ModelManifest(files: repeatedEntries, expectedDigest: ModelManifest.hash(Data(repeatedJSON.utf8)))
        precondition(ModelStore.requiredImportBytes(manifest: repeated, cachedHashes: []) == headroom + 5); checks += 1
        precondition(ModelStore.requiredImportBytes(manifest: repeated, cachedHashes: [hash]) == headroom + 2); checks += 1
        let repeatedSource = root.appendingPathComponent("repeated-source")
        try fm.createDirectory(at: repeatedSource, withIntermediateDirectories: false)
        for name in ["a", "b"] { try payload.write(to: repeatedSource.appendingPathComponent(name)) }
        let repeatedModel = try store.importModel(source: repeatedSource, modelID: "repeated", manifest: repeated)
        try ModelTreeVerifier.verify(root: repeatedModel, manifest: repeated); checks += 1
        let reusedRepeated = try store.importModel(source: root.appendingPathComponent("absent"), modelID: "repeated-again", manifest: repeated)
        try ModelTreeVerifier.verify(root: reusedRepeated, manifest: repeated); checks += 1
        precondition(ModelStore.validModelID(QwenModelCatalog.modelID)); checks += 1
        for invalid in ["..", ".hidden", "trailing.", "a..b", "a/b"] {
            precondition(!ModelStore.validModelID(invalid)); checks += 1
        }
        let pinned = try QwenModelCatalog.manifest()
        precondition(pinned.files.count == 9 && pinned.totalBytes > 1_800_000_000); checks += 1
        precondition(!pinned.files.contains { ["LICENSE", "SOURCE.txt"].contains($0.path) }); checks += 1
        let resolved = try ASRComponentResolver.verifiedModel(in: storeRoot, modelID: "repeated", manifest: repeated)
        precondition(resolved.path == repeatedModel.path); checks += 1
        rejects { _ = try ASRComponentResolver.verifiedModel(in: storeRoot, modelID: "not-installed", manifest: repeated) }
        rejects { _ = try ASRComponentResolver.verifiedModel(in: storeRoot, modelID: "test-model", manifest: largeManifest) }
        rejects { _ = try ASRComponentResolver.verifiedModel(in: storeRoot, modelID: "../escape", manifest: repeated) }
        rejects { _ = try ASRComponentResolver.qwenModel(in: storeRoot) }
        // A standard, public OS fixture is root-owned: no employee/user content is read.
        let foreignRoot = URL(fileURLWithPath: "/usr/share/misc", isDirectory: true)
        let ascii = try Data(contentsOf: foreignRoot.appendingPathComponent("ascii"))
        let asciiHash = ModelManifest.hash(ascii)
        let asciiJSON = "[{\"path\":\"ascii\",\"sha256\":\"\(asciiHash)\",\"size\":\(ascii.count)}]"
        let foreignManifest = try ModelManifest(files: [.init(path: "ascii", size: Int64(ascii.count), sha256: asciiHash)], expectedDigest: ModelManifest.hash(Data(asciiJSON.utf8)))
        let foreignCopy = try ModelTreeVerifier.stage(root: foreignRoot, manifest: foreignManifest, stagingParent: root)
        defer { try? fm.removeItem(at: foreignCopy) }
        try ModelTreeVerifier.verify(root: foreignCopy, manifest: foreignManifest); checks += 1
        if geteuid() != 0 { rejects { try ModelTreeVerifier.verify(root: foreignRoot, manifest: foreignManifest) } }
        precondition(lstat(foreignCopy.appendingPathComponent("ascii").path, &info) == 0 && info.st_uid == geteuid()); checks += 1
        let duplicateLink = root.appendingPathComponent("duplicate-link")
        try fm.linkItem(at: config, to: duplicateLink)
        rejects { try ModelTreeVerifier.verify(root: root, manifest: largeManifest) }
        let linkedCopy = try ModelTreeVerifier.stage(root: root, manifest: largeManifest, stagingParent: root)
        defer { try? fm.removeItem(at: linkedCopy) }
        try ModelTreeVerifier.verify(root: linkedCopy, manifest: largeManifest); checks += 1
        precondition(lstat(linkedCopy.appendingPathComponent("config.json").path, &info) == 0 && info.st_nlink == 1); checks += 1
        try Data("source changed".utf8).write(to: config)
        try ModelTreeVerifier.verify(root: linkedCopy, manifest: largeManifest); checks += 1
        try large.write(to: config)
        precondition(chmod(config.path, 0o666) == 0)
        rejects { _ = try ModelTreeVerifier.stage(root: root, manifest: largeManifest, stagingParent: root) }
        rejects { _ = try ModelTreeVerifier.stage(root: alias, manifest: largeManifest, stagingParent: root) }
        precondition(ASRComponentResolver.defaultRoot(applicationSupport: root).path == root.appendingPathComponent("VoxLog/Components").path); checks += 1
        let missingManaged = try ASRComponentResolver.managedModelIfPresent(in: root.appendingPathComponent("missing-store"), modelID: "repeated", manifest: repeated)
        precondition(missingManaged == nil); checks += 1
        let managed = try ASRComponentResolver.managedModelIfPresent(in: storeRoot, modelID: "repeated", manifest: repeated)
        precondition(managed == repeatedModel); checks += 1
        rejects { _ = try ASRComponentResolver.managedModelIfPresent(in: storeRoot, modelID: "missing", manifest: repeated) }
        let dangling = root.appendingPathComponent("dangling-store")
        try fm.createSymbolicLink(at: dangling, withDestinationURL: root.appendingPathComponent("absent"))
        rejects { _ = try ASRComponentResolver.managedModelIfPresent(in: dangling, modelID: "repeated", manifest: repeated) }
        let cancelled = await Task.detached {
            withUnsafeCurrentTask { $0?.cancel() }
            do {
                try ModelTreeVerifier.verify(root: repeatedModel, manifest: repeated)
                return false
            } catch is CancellationError { return true }
            catch { return false }
        }.value
        precondition(cancelled); checks += 1
        print("Model manifest and descriptor verification: \(checks) checks passed")
    }
}
#endif
