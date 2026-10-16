#if MODEL_IMPORT_CLI
import Foundation
import Darwin

@main enum ModelImportCLI {
    /// Stable, actionable diagnostics without interpolating filesystem paths or raw errors.
    static func diagnostic(_ error: Error) -> String {
        switch error {
        case ModelStore.Failure.busy:
            return "[busy] Another import owns the store lock. Wait for it to finish; do not remove the lock file."
        case ModelStore.Failure.insufficientSpace, ModelTreeVerifier.Failure.insufficientSpace:
            return "[space] Insufficient free space on the store volume. Free space or select another private store."
        case ModelTreeVerifier.Failure.hashMismatch, ModelTreeVerifier.Failure.sizeMismatch:
            return "[integrity] Model bytes do not match the pinned revision. Obtain a complete matching model source and retry."
        case ModelStore.Failure.unsafeStore, ModelTreeVerifier.Failure.unsafeRoot, ModelTreeVerifier.Failure.unsafeFile:
            return "[unsafe-path] A path is missing or fails ownership/file safety checks. Use an existing private store and regular, non-symlink model files."
        case ModelTreeVerifier.Failure.readFailed:
            return "[read] Model data could not be read. Check source availability and access, then retry."
        case ModelStore.Failure.publicationFailed, ModelTreeVerifier.Failure.stagingFailed, ModelTreeVerifier.Failure.writeFailed:
            return "[write] Staging or publication failed. Check store availability and write access; preserve existing revisions."
        case is CancellationError:
            return "[cancelled] Model operation cancelled. Existing published revisions are retained."
        default:
            return "[invalid] Model operation could not complete. Check the pinned manifest and store layout; no verified receipt was produced."
        }
    }

    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            guard (args.count == 3 && ["import", "media"].contains(args[0])) || (args.count == 2 && args[0] == "verify") else {
                fputs("Usage: model-import import EXISTING_STORE SOURCE | media EXISTING_PRIVATE_OUTPUT_PARENT SOURCE | verify EXISTING_STORE\n", stderr)
                exit(2)
            }
            let store = URL(fileURLWithPath: args[1], isDirectory: true)
            let result: URL
            if args[0] == "media" {
                // A standalone directory for the native importer; no runtime, App or server manifest.
                result = try ModelTreeVerifier.stage(root: URL(fileURLWithPath: args[2], isDirectory: true),
                    manifest: QwenModelCatalog.manifest(), stagingParent: store)
            } else if args[0] == "import" {
                result = try ModelStore(root: store).importModel(source: URL(fileURLWithPath: args[2], isDirectory: true),
                    modelID: QwenModelCatalog.modelID, manifest: QwenModelCatalog.manifest())
            } else {
                result = try ASRComponentResolver.qwenModel(in: store)
            }
            let report = ["operation": args[0], "model_id": QwenModelCatalog.modelID, "revision": QwenModelCatalog.revision,
                          "digest": QwenModelCatalog.digest, "verified_path": result.path]
            let json = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
            print(String(decoding: json, as: UTF8.self))
        } catch {
            fputs("Model operation failed: \(diagnostic(error))\n", stderr)
            exit(1)
        }
    }
}
#endif
