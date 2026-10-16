import Foundation
import os

/// Fetches available ASR models from provider APIs.
/// Caches results to ~/Library/Application Support/VoxLog/asr-model-catalog.json
actor ASRModelListFetcher {

    static let shared = ASRModelListFetcher()
    private let logger = Logger(subsystem: "com.voxlog.models", category: "ASRModelListFetcher")

    private let cacheURL: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("asr-model-catalog.json")
    }()

    private static let providers: [(provider: ASRProvider, baseURL: String, credKey: String)] = [
        (.openai,     "https://api.openai.com/v1",                               "tf_asr_openai"),
        (.bailian,    "https://dashscope.aliyuncs.com/compatible-mode/v1",        "tf_asr_bailian"),
        (.bailianUS,  "https://dashscope-us.aliyuncs.com/compatible-mode/v1",     "tf_asr_bailianUS"),
        (.deepgram,   "https://api.deepgram.com/v1",                              "tf_asr_deepgram"),
        (.assemblyai, "https://api.assemblyai.com/v2",                            "tf_asr_assemblyai"),
    ]

    // MARK: - Fetch All

    func refreshAll() async -> [String: [String]] {
        var catalog = loadCache()

        for entry in Self.providers {
            let creds = KeychainService.loadASRCredentials(for: entry.provider)
            guard let apiKey = creds?["apiKey"], !apiKey.isEmpty else { continue }

            let baseURL = creds?["baseURL"]?.isEmpty == false ? creds!["baseURL"]! : entry.baseURL
            let models = await fetchModels(baseURL: baseURL, apiKey: apiKey, provider: entry.provider)

            if !models.isEmpty {
                catalog[entry.provider.rawValue] = models
                logger.info("ASR: fetched \(models.count) models from \(entry.provider.rawValue)")
            }
        }

        saveCache(catalog)
        return catalog
    }

    // MARK: - Fetch

    private func fetchModels(baseURL: String, apiKey: String, provider: ASRProvider) async -> [String] {
        guard let url = URL(string: "\(baseURL)/models") else { return [] }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200 else { return [] }
            return parseAndFilter(data: data, provider: provider)
        } catch {
            logger.warning("ASR \(provider.rawValue) fetch error: \(error.localizedDescription)")
            return []
        }
    }

    private func parseAndFilter(data: Data, provider: ASRProvider) -> [String] {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let items = json["data"] as? [[String: Any]]
        else { return [] }

        let allIds = items.compactMap { $0["id"] as? String }

        switch provider {
        case .openai:
            return allIds.filter { $0.contains("whisper") || $0.contains("transcri") }.sorted()
        case .bailian, .bailianUS:
            return allIds.filter { $0.contains("asr") || $0.contains("paraformer") || $0.contains("sensevoice") || $0.contains("fun-asr") }.sorted()
        default:
            return allIds.filter { $0.contains("asr") || $0.contains("speech") || $0.contains("transcri") || $0.contains("whisper") }.sorted()
        }
    }

    // MARK: - Cache

    func loadCache() -> [String: [String]] {
        guard let data = try? Data(contentsOf: cacheURL),
              let dict = try? JSONDecoder().decode([String: [String]].self, from: data)
        else { return [:] }
        return dict
    }

    private func saveCache(_ catalog: [String: [String]]) {
        if let data = try? JSONEncoder().encode(catalog) {
            try? data.write(to: cacheURL, options: .atomic)
        }
    }

    func models(for provider: ASRProvider) -> [String] {
        loadCache()[provider.rawValue] ?? []
    }
}
