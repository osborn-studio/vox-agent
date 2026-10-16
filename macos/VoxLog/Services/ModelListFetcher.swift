import Foundation
import os

/// Fetches available models from each LLM provider's /v1/models endpoint.
/// Caches results to ~/Library/Application Support/VoxLog/model-catalog.json
actor ModelListFetcher {

    static let shared = ModelListFetcher()
    private let logger = Logger(subsystem: "com.voxlog.models", category: "ModelListFetcher")

    private let cacheURL: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("model-catalog.json")
    }()

    /// Provider configs: name → (baseURL, apiKeyCredPath)
    private static let providers: [(provider: LLMProvider, baseURL: String, credKey: String)] = [
        // International
        (.openai,     "https://api.openai.com/v1",                                     "tf_llm_openai"),
        (.gemini,     "https://generativelanguage.googleapis.com/v1beta/openai",        "tf_llm_gemini"),
        (.claude,     "https://api.anthropic.com/v1",                                   "tf_llm_claude"),
        // China
        (.bailian,    "https://dashscope.aliyuncs.com/compatible-mode/v1",              "tf_llm_bailian"),
        (.bailianUS,  "https://dashscope-us.aliyuncs.com/compatible-mode/v1",           "tf_llm_bailianUS"),
        (.kimi,       "https://api.moonshot.ai/v1",                                     "tf_llm_kimi"),
        (.doubao,     "https://ark.cn-beijing.volces.com/api/v3",                       "tf_llm_doubao"),
        (.deepseek,   "https://api.deepseek.com",                                      "tf_llm_deepseek"),
        (.zhipu,      "https://open.bigmodel.cn/api/paas/v4",                           "tf_llm_zhipu"),
        (.minimaxCN,  "https://api.minimaxi.com/v1",                                   "tf_llm_minimaxCN"),
    ]

    // MARK: - Fetch All

    /// Fetch models from all configured providers. Returns updated catalog.
    func refreshAll() async -> [String: [String]] {
        var catalog: [String: [String]] = loadCache()

        for entry in Self.providers {
            let creds = KeychainService.loadLLMCredentials(for: entry.provider)
            guard let apiKey = creds?["apiKey"], !apiKey.isEmpty else { continue }

            let baseURL = creds?["baseURL"]?.isEmpty == false ? creds!["baseURL"]! : entry.baseURL
            let models = await fetchModels(baseURL: baseURL, apiKey: apiKey, provider: entry.provider)

            if !models.isEmpty {
                catalog[entry.provider.rawValue] = models
                logger.info("Fetched \(models.count) models from \(entry.provider.rawValue)")
            }
        }

        saveCache(catalog)
        return catalog
    }

    func refresh(provider: LLMProvider, credentials: [String: String]) async -> [String] {
        guard let entry = Self.providers.first(where: { $0.provider == provider }) else { return [] }
        let apiKey = credentials["apiKey"]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !apiKey.isEmpty || !provider.requiresAPIKey else { return [] }

        let baseURL = credentials["baseURL"]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? credentials["baseURL"]!
            : entry.baseURL
        let models = await fetchModels(baseURL: baseURL, apiKey: apiKey, provider: provider)

        if !models.isEmpty {
            var catalog = loadCache()
            catalog[provider.rawValue] = models
            saveCache(catalog)
            logger.info("Fetched \(models.count) models from \(provider.rawValue)")
        }

        return models
    }

    // MARK: - Fetch Single Provider

    private func fetchModels(baseURL: String, apiKey: String, provider: LLMProvider) async -> [String] {
        // Anthropic uses a different endpoint format
        let url: URL?
        if provider == .claude {
            url = URL(string: "\(baseURL)/models")
        } else {
            url = URL(string: "\(baseURL)/models")
        }

        guard let url else { return [] }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 10

        // Auth header
        if provider == .claude {
            request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
            request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        } else if provider == .gemini {
            // Gemini uses API key in URL or header
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        } else {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200 else {
                logger.warning("\(provider.rawValue) models fetch failed: HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")
                return []
            }

            return Self.parseModelsResponse(data: data, provider: provider)
        } catch {
            logger.warning("\(provider.rawValue) models fetch error: \(error.localizedDescription)")
            return []
        }
    }

    static func parseModelsResponse(data: Data, provider: LLMProvider) -> [String] {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [] }

        var modelIds: [String] = []

        // OpenAI format: { "data": [ {"id": "gpt-4o"}, ... ] }
        if let items = json["data"] as? [[String: Any]] {
            for item in items {
                if let id = item["id"] as? String {
                    modelIds.append(id)
                }
            }
        }
        // Anthropic format: { "data": [ {"id": "claude-sonnet-4-20250514"}, ... ] }
        // Same structure, already handled above

        // Filter relevant models based on provider
        return filterModels(modelIds, for: provider)
    }

    private static func filterModels(_ models: [String], for provider: LLMProvider) -> [String] {
        switch provider {
        case .openai:
            return models.filter { $0.contains("gpt") || $0.contains("o1") || $0.contains("o3") || $0.contains("o4") }
                .sorted()
        case .gemini:
            return models.filter { $0.contains("gemini") }.sorted()
        case .claude:
            return models.filter { $0.contains("claude") }.sorted()
        case .bailian:
            return models.sorted()
        case .bailianUS:
            return models.filter { $0.contains("qwen") }.sorted()
        case .kimi:
            return models.filter { $0.contains("moonshot") || $0.contains("kimi") }.sorted()
        case .doubao:
            return models.filter { $0.contains("doubao") || $0.contains("seed") }.sorted()
        case .deepseek:
            return models.filter { $0.contains("deepseek") }.sorted()
        case .zhipu:
            return models.filter { $0.contains("glm") || $0.contains("chatglm") }.sorted()
        case .minimaxCN, .minimaxIntl:
            return models.filter { $0.contains("minimax") || $0.contains("abab") }.sorted()
        default:
            return models.sorted()
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

    /// Get cached models for a specific provider.
    func models(for provider: LLMProvider) -> [String] {
        loadCache()[provider.rawValue] ?? []
    }
}
