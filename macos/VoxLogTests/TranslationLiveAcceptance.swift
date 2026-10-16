#if TRANSLATION_ACCEPTANCE
import Foundation

@main struct TranslationLiveAcceptance {
    struct Sample: Decodable, Sendable { let id: String; let source: String; let reference_zh: String; let review: String }
    struct Result: Codable, Sendable {
        let id: String; let source: String; let reference: String; let review: String
        let output: String?; let status: String; let seconds: Double
    }
    struct Report: Codable {
        let provider: String; let model: String; let preferencesDomain: String
        let startedAt: String; let expectedSamples: Int; let results: [Result]
    }
    @MainActor static func run(_ sample: Sample, _ config: TranslationConfiguration) async -> Result {
        let start = Date()
        do {
            let service = MessageTranslationService(snapshot: { config }, completion: {
                try await TranslationTransport.complete(source: $0, config: $1)
            })
            let protection = try service.validate(sample.source)
            if protection.onlyCode {
                return Result(id: sample.id, source: sample.source, reference: sample.reference_zh, review: sample.review, output: nil, status: "code_only_no_request", seconds: 0)
            }
            let response = try await service.completion(sample.source, try service.snapshot())
            try protection.validate(response.text)
            return Result(id: sample.id, source: sample.source, reference: sample.reference_zh, review: sample.review, output: response.text, status: "complete_pending_semantic_review", seconds: Date().timeIntervalSince(start))
        } catch {
            let code = (error as? TranslationFailure).map { String(describing: $0) } ?? "unexpected_error"
            return Result(id: sample.id, source: sample.source, reference: sample.reference_zh, review: sample.review, output: nil, status: code, seconds: Date().timeIntervalSince(start))
        }
    }
    @MainActor static func main() async throws {
        umask(0o077)
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        let samples = try JSONDecoder().decode([Sample].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])))
        guard samples.count == 30 else { fatalError("expected committed 30 sample fixture") }
        // Read the installed Vox preference domain, never migrate or change it.
        let defaults = UserDefaults(suiteName: CommandLine.arguments[3])!
        guard let raw = defaults.string(forKey: "tf_selectedLLMProvider"), let provider = LLMProvider(rawValue: raw) else {
            print("BLOCKED: installed Vox has no explicit selected Polish provider"); exit(2)
        }
        let config: TranslationConfiguration
        do {
            config = try SelectedLLMConfigurationResolver.snapshot(selected: { provider }, load: { KeychainService.loadLLMCredentials(for: $0) }, bypass: { ProxyBypassMode(rawValue: defaults.string(forKey: "tf_bypassProxy") ?? "off")?.bypassLLM ?? false })
            _ = try TranslationProtocol.request("synthetic", config: config)
        } catch { print("BLOCKED: selected model configuration unavailable or unsupported; no provider call made"); exit(2) }
        guard !FileManager.default.fileExists(atPath: output.appendingPathComponent("live-results.json").path) else {
            print("BLOCKED: existing results; choose a new output directory to avoid accidental repeat calls"); exit(2)
        }
        let startedAt = ISO8601DateFormatter().string(from: Date())
        var results: [Result] = []
        // Exclusive creation claims this output directory before any paid request.
        // Even a crash during the first batch cannot silently repeat it on rerun.
        let initial = try JSONEncoder().encode(Report(provider: provider.rawValue, model: config.model, preferencesDomain: CommandLine.arguments[3], startedAt: startedAt, expectedSamples: samples.count, results: []))
        try initial.write(to: output.appendingPathComponent("live-results.json"), options: .withoutOverwriting)
        print("Selected provider: \(provider.rawValue); synthetic samples: \(samples.count)")
        for index in stride(from: 0, to: samples.count, by: 2) {
            let batch = Array(samples[index..<min(index + 2, samples.count)])
            let values = await withTaskGroup(of: Result.self) { group in
                for sample in batch { group.addTask { await run(sample, config) } }
                var values: [Result] = []
                for await value in group { values.append(value) }
                return values
            }
            results += values
            let data = try JSONEncoder().encode(Report(provider: provider.rawValue, model: config.model, preferencesDomain: CommandLine.arguments[3], startedAt: startedAt, expectedSamples: samples.count, results: results))
            let path = output.appendingPathComponent("live-results.json")
            try data.write(to: path, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            for result in values { print("\(result.id): \(result.status) (\(String(format: "%.2f", result.seconds))s)") }
        }
        if results.contains(where: { !["complete_pending_semantic_review", "code_only_no_request"].contains($0.status) }) { exit(1) }
    }
}
#endif
