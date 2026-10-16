import Foundation

struct ModeStorage {

    let fileURL: URL

    init(fileURL: URL? = nil) {
        if let url = fileURL {
            self.fileURL = url
        } else {
            let appSupport = FileManager.default.urls(
                for: .applicationSupportDirectory, in: .userDomainMask
            ).first!.appendingPathComponent("VoxLog", isDirectory: true)
            try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
            self.fileURL = appSupport.appendingPathComponent("modes.json")
        }
    }

    func save(_ modes: [ProcessingMode]) throws {
        let data = try JSONEncoder().encode(modes)
        try data.write(to: fileURL, options: .atomic)
    }

    func load() -> [ProcessingMode] {
        guard let data = try? Data(contentsOf: fileURL),
              let saved = try? JSONDecoder().decode([ProcessingMode].self, from: data),
              !saved.isEmpty
        else {
            return ProcessingMode.defaults
        }

        // Migrate legacy built-in flags for default modes, and drop unknown built-ins.
        var result = saved.compactMap { mode -> ProcessingMode? in
            if mode.id == ProcessingMode.directId {
                var d = ProcessingMode.direct
                d.hotkeyCode = mode.hotkeyCode
                d.hotkeyModifiers = mode.hotkeyModifiers
                d.hotkeyStyle = mode.hotkeyStyle
                return d
            }
            if mode.id == ProcessingMode.smartDirectId {
                return migrateDefaultMode(mode, fallback: .smartDirect)
            }
            if mode.id == ProcessingMode.translateId {
                return migrateDefaultMode(mode, fallback: .translate)
            }
            if mode.id == ProcessingMode.formalWriting.id {
                return migrateSeededDefaultPrompt(
                    mode,
                    legacyPrompts: [ProcessingMode.legacyFormalWritingPromptTemplate],
                    fallbackPrompt: ProcessingMode.formalWriting.prompt
                )
            }
            if mode.id == ProcessingMode.translate.id {
                return migrateSeededDefaultPrompt(
                    mode,
                    legacyPrompts: [ProcessingMode.legacyTranslatePromptTemplate],
                    fallbackPrompt: ProcessingMode.translate.prompt
                )
            }
            // Drop legacy dual-channel mode (replaced by global "enhanced ASR" toggle)
            if mode.id == UUID(uuidString: "00000000-0000-0000-0000-000000000007")! {
                return nil
            }
            if mode.isBuiltin {
                return nil
            }
            return mode
        }

        // Ensure required built-in modes always exist.
        let resultIds = Set(result.map(\.id))
        for builtin in ProcessingMode.builtins where !resultIds.contains(builtin.id) {
            if let idx = ProcessingMode.builtins.firstIndex(where: { $0.id == builtin.id }) {
                let insertAt = min(idx, result.count)
                result.insert(builtin, at: insertAt)
            } else {
                result.append(builtin)
            }
        }

        return result
    }

    private func migrateDefaultMode(_ mode: ProcessingMode, fallback: ProcessingMode) -> ProcessingMode {
        guard mode.isBuiltin || mode.prompt.isEmpty else { return mode }

        var migrated = fallback
        if !mode.name.isEmpty {
            migrated.name = mode.name
        }
        if !mode.processingLabel.isEmpty {
            migrated.processingLabel = mode.processingLabel
        }
        migrated.hotkeyCode = mode.hotkeyCode
        migrated.hotkeyModifiers = mode.hotkeyModifiers
        migrated.hotkeyStyle = mode.hotkeyStyle
        migrated.isBuiltin = false
        return migrated
    }

    private func migrateSeededDefaultPrompt(
        _ mode: ProcessingMode,
        legacyPrompts: Set<String>,
        fallbackPrompt: String
    ) -> ProcessingMode {
        guard legacyPrompts.contains(mode.prompt) else { return mode }

        var migrated = mode
        migrated.prompt = fallbackPrompt
        migrated.isBuiltin = false
        return migrated
    }
}
