#if MESSAGE_TRANSLATION_HARNESS
import Foundation

private func check(_ value: Bool, _ label: String) {
    precondition(value, label)
}
private func rejects(_ label: String, _ body: () throws -> Void) {
    do { try body(); preconditionFailure(label) } catch {}
}

/// All URLSession traffic is intercepted. Any unexpected request fails the test.
final class TranslationWire: URLProtocol, @unchecked Sendable {
    static let lock = NSLock()
    static var status = 200
    static var chunks: [Data] = []
    static var hold = false
    static var trickle = false
    private var timer: DispatchSourceTimer?
    static var requests: [URLRequest] = []
    static var stopped = 0
    static func setup(_ body: Data = Data(), status: Int = 200, hold: Bool = false, trickle: Bool = false, chunks: [Data]? = nil) {
        lock.lock(); defer { lock.unlock() }
        self.status = status; self.chunks = chunks ?? [body]; self.hold = hold; self.trickle = trickle; requests = []; stopped = 0
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        Self.requests.append(request)
        let status = Self.status, chunks = Self.chunks, hold = Self.hold, trickle = Self.trickle
        Self.lock.unlock()
        guard request.url?.host == "fixture.test" else { preconditionFailure("unexpected network destination") }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client!.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if trickle {
            let timer = DispatchSource.makeTimerSource()
            timer.schedule(deadline: .now(), repeating: .milliseconds(15))
            timer.setEventHandler { [weak self] in
                guard let self else { return }
                self.client?.urlProtocol(self, didLoad: Data(" ".utf8))
            }
            self.timer = timer; timer.resume(); return
        }
        if hold { return }
        for chunk in chunks { client!.urlProtocol(self, didLoad: chunk) }
        client!.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {
        timer?.cancel(); timer = nil
        Self.lock.lock(); Self.stopped += 1; Self.lock.unlock()
    }
    static var stopCount: Int { lock.lock(); defer { lock.unlock() }; return stopped }
    static var requestCount: Int { lock.lock(); defer { lock.unlock() }; return requests.count }
}

@main struct MessageTranslationHarness {
    @MainActor static func wait(_ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(3)
        while !condition() && Date() < deadline { await Task.yield() }
        check(condition(), "bounded asynchronous wait")
    }
    static func config(_ provider: LLMProvider = .openai, generation: UInt64 = 0) throws -> TranslationConfiguration {
        try TranslationConfiguration(provider: provider, baseURL: "https://fixture.test/v1/", model: provider == .claude ? "claude-sonnet-4-6" : provider == .deepseek ? "deepseek-flash" : [.bailian, .bailianUS].contains(provider) ? "qwen3.6-plus" : "gpt-4.1-mini", credential: "synthetic", generation: generation, bypassProxy: true)
    }
    static func wireBody(_ text: String = "译文", reason: Any = "stop", extra: [String: Any] = [:]) throws -> Data {
        var message: [String: Any] = ["role": "assistant", "content": text]
        message.merge(extra) { _, new in new }
        return try JSONSerialization.data(withJSONObject: ["choices": [["finish_reason": reason, "message": message]]])
    }
    static func protection() throws {
        let fixtures = [
            "Use `x` and https://example.test/a.",
            "Before\n```swift\nlet x = 1\n```\nAfter",
            "Before\n````md\n```inner```\n````\nAfter",
            "Keep ``a ` b`` and `c`.",
            "Use (https://例子.test/路径)，then https://a.test/?x=1&y=2。",
            "{text} <think>literal</think>",
            "~~~python\n# 不翻译 comments\n~~~",
            "```a\nx\n```\n```b\ny\n```",
            "`e\u{301}` `é`"
        ]
        for text in fixtures { try TranslationProtection(text).validate(text) }
        let repeated = try TranslationProtection("A `x` `x` https://a.test")
        try repeated.validate("https://a.test，`x` 后 `x`")
        for bad in ["`x` https://a.test", "`x` `x` `x` https://a.test", "`y` `x` https://a.test", "`x` `x` https://b.test"] {
            rejects("protected byte multiset") { try repeated.validate(bad) }
        }
        for text in ["```swift\nmissing", "`missing", "``a`", "```swift`bad\nx\n```"] {
            rejects("unclosed/ambiguous code") { _ = try TranslationProtection(text) }
        }
        rejects("Unicode normalization is not byte equality") { try TranslationProtection("`é`").validate("`e\u{301}`") }
        check(try TranslationProtection("\n````swift\nx\n````\n").onlyCode, "pure fenced code")
        check(!(try TranslationProtection("`x` https://a.test").onlyCode), "outside URL is not pure code")
        check(!(try TranslationProtection("说明 `x`").onlyCode), "mixed prose")
        let url = try TranslationProtection("See https://a.test/x now")
        try url.validate("参见 https://a.test/x 获取")
        rejects("ambiguous CJK URL boundary fails closed") { try url.validate("参见https://a.test/x获取") }
        let mention = try TranslationProtection("Ask @_user_12 <at user_id=\"ou_fixture\">Name</at>")
        try mention.validate("请问 @_user_12 <at user_id=\"ou_fixture\">Name</at>")
        rejects("mention identity cannot change") { try mention.validate("请问 @_user_13 <at user_id=\"ou_fixture\">Name</at>") }
        let started = Date()
        _ = try TranslationProtection(String(repeating: "https://a.test/x ", count: 4000))
        check(Date().timeIntervalSince(started) < 2, "bounded URL scan at output cap")
        print("PASS protection fixtures and byte multisets")
    }
    static func protocols() async throws {
        let c = try config()
        let request = try TranslationProtocol.request("literal {text} <think>x</think>", config: c)
        let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
        check(body["stream"] as? Bool == false && body["max_completion_tokens"] as? Int == 4096, "bounded nonstream request")
        let messages = body["messages"] as! [[String: String]]
        check(messages.count == 2 && messages[1]["content"] == "literal {text} <think>x</think>", "independent literal user field")
        check(body["tools"] == nil && body["temperature"] == nil, "no tool effects or model guessing")
        let claude = try config(.claude)
        let cb = try JSONSerialization.jsonObject(with: TranslationProtocol.request("hello", config: claude).httpBody!) as! [String: Any]
        check(cb["system"] as? String == TranslationProtocol.instruction, "Anthropic system field")
        check((cb["messages"] as! [[String: String]]).count == 1, "Anthropic user field")
        check((cb["thinking"] as! [String: String])["type"] == "disabled", "thinking disabled")
        let anthropic = try JSONSerialization.data(withJSONObject: ["type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [["type": "text", "text": "<think>字面</think>"]]])
        check(try TranslationProtocol.decode(anthropic, provider: .claude).text == "<think>字面</think>", "literal tags are never stripped")
        for reason in ["length", "content_filter", "tool_calls", "", "max_tokens"] {
            rejects("non-final completion") { _ = try TranslationProtocol.decode(wireBody(reason: reason), provider: .openai) }
        }
        rejects("missing finish reason") { _ = try TranslationProtocol.decode(wireBody(reason: NSNull()), provider: .openai) }
        rejects("protocol refusal") { _ = try TranslationProtocol.decode(wireBody(extra: ["refusal": "private"]), provider: .openai) }
        rejects("tool call") { _ = try TranslationProtocol.decode(wireBody(extra: ["tool_calls": []]), provider: .openai) }
        rejects("empty") { _ = try TranslationProtocol.decode(wireBody(" \n"), provider: .openai) }
        rejects("output byte limit") { _ = try TranslationProtocol.decode(wireBody(String(repeating: "中", count: 22_000)), provider: .openai) }
        rejects("broken JSON") { _ = try TranslationProtocol.decode(Data("{unfinished".utf8), provider: .openai) }
        for provider in LLMProvider.allCases {
            let models = provider.modelOptions.isEmpty ? ["fixture-model"] : provider.modelOptions.map(\.value)
            for model in models {
                let profile = try TranslationConfiguration(provider: provider, baseURL: provider.defaultBaseURL,
                    model: model, credential: "synthetic", generation: 0, bypassProxy: false)
                let request = try TranslationProtocol.request("literal input", config: profile)
                let object = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                check(object["stream"] as? Bool == false, "each settings provider is nonstreaming")
                check((object["max_tokens"] as? Int ?? object["max_completion_tokens"] as? Int) == 4096, "token budget for every profile")
                check(request.url?.absoluteString == provider.defaultBaseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + (provider == .claude ? "/messages" : "/chat/completions"), "configured base path parity")
                if [.minimaxCN, .minimaxIntl].contains(provider) { check(object["reasoning_split"] as? Bool == true, "MiniMax structured reasoning") }
                if provider == .doubao { check((object["thinking"] as? [String: String])?["type"] == "disabled", "default Doubao supported") }
            }
            if provider != .claude {
                _ = try TranslationProtocol.decode(wireBody(extra: ["tool_calls": NSNull(), "function_call": NSNull(), "refusal": NSNull(), "reasoning_content": "excluded"]), provider: provider)
            }
        }
        check(!TranslationProtocol.supports(.openai, model: "gpt-4o-realtime-preview"), "reject non-chat endpoint model")
        let omni = try TranslationConfiguration(provider: .bailian, baseURL: LLMProvider.bailian.defaultBaseURL,
            model: "qwen3.8-omni-flash", credential: "synthetic", generation: 0, bypassProxy: false)
        let omniRequest = try TranslationProtocol.request("literal text", config: omni)
        let omniBody = try JSONSerialization.jsonObject(with: omniRequest.httpBody!) as! [String: Any]
        check(omniBody["reasoning_effort"] as? String == "none" && omniBody["enable_thinking"] == nil,
              "Qwen 3.8 uses reasoning_effort, not legacy thinking toggle")
        check(omniBody["modalities"] as? [String] == ["text"], "Omni requests text only")
        check(!TranslationProtocol.supports(.bailian, model: "qwen3.8-omni-flash-realtime"), "reject realtime protocol")
        rejects("remote cleartext") { _ = try TranslationConfiguration(provider: .ollama, baseURL: "http://remote.test/v1", model: "m", credential: "", generation: 0, bypassProxy: false) }
        rejects("userinfo") { _ = try TranslationConfiguration(provider: .openai, baseURL: "https://key@fixture.test/v1", model: "m", credential: "x", generation: 0, bypassProxy: false) }
        rejects("query credentials") { _ = try TranslationConfiguration(provider: .openai, baseURL: "https://fixture.test/v1?key=x", model: "m", credential: "x", generation: 0, bypassProxy: false) }
        let sessionConfig: (URLSessionConfiguration) -> Void = { config in
            check(config.urlCache == nil && config.httpCookieStorage == nil, "ephemeral no persistence")
            check(config.connectionProxyDictionary?.isEmpty == true, "explicit LLM proxy bypass")
            config.protocolClasses = [TranslationWire.self]
        }
        TranslationWire.setup(try wireBody("<think>字面</think>"))
        let output = try await TranslationTransport.complete(source: "synthetic", config: c, configure: sessionConfig)
        check(output.complete && output.text == "<think>字面</think>", "actual production URLSession + decode")
        check(TranslationWire.requestCount == 1, "one request, no retry")
        for status in [301, 302, 307, 401, 429, 500] {
            TranslationWire.setup(Data("private provider diagnostics".utf8), status: status)
            do { _ = try await TranslationTransport.complete(source: "synthetic", config: c, configure: sessionConfig); preconditionFailure("HTTP rejection") } catch {}
            check(TranslationWire.requestCount == 1, "no redirects or retries")
        }
        let delegate = TranslationTransport()
        let redirectSession = URLSession(configuration: .ephemeral)
        let redirectTask = redirectSession.dataTask(with: request)
        var redirectFollowed = true
        delegate.urlSession(redirectSession, task: redirectTask, willPerformHTTPRedirection: HTTPURLResponse(url: c.endpoint, statusCode: 307, httpVersion: nil, headerFields: nil)!, newRequest: URLRequest(url: URL(string: "https://different.test")!)) { redirectFollowed = $0 != nil }
        check(!redirectFollowed, "delegate refuses credential-bearing redirects")
        redirectSession.invalidateAndCancel()
        TranslationWire.setup(chunks: [Data(repeating: 32, count: 200_000), Data(repeating: 32, count: 100_000)])
        do { _ = try await TranslationTransport.complete(source: "synthetic", config: c, configure: sessionConfig); preconditionFailure("body cap without content length") } catch {}
        TranslationWire.setup(trickle: true)
        let begin = Date()
        do { _ = try await TranslationTransport.complete(source: "synthetic", config: c, deadline: 0.08, configure: sessionConfig); preconditionFailure("deadline") } catch {}
        check(Date().timeIntervalSince(begin) < 2, "total deadline wall clock")
        await wait { TranslationWire.stopCount > 0 }
        TranslationWire.setup(hold: true)
        let task = Task { try await TranslationTransport.complete(source: "synthetic", config: c, configure: sessionConfig) }
        await wait { TranslationWire.requestCount == 1 }
        task.cancel()
        do { _ = try await task.value; preconditionFailure("cancellation") } catch {}
        await wait { TranslationWire.stopCount > 0 }
        let already = Task { try await TranslationTransport.complete(source: "synthetic", config: c, configure: sessionConfig) }
        already.cancel()
        do { _ = try await already.value; preconditionFailure("cancel before start") } catch {}
        print("PASS request/finish/refusal/body/redirect/timeout/URLSession cancellation")
    }
    @MainActor static func configuration() throws {
        var selectedCalls = 0, loads: [LLMProvider] = []
        let captured = try SelectedLLMConfigurationResolver.snapshot(selected: { selectedCalls += 1; return .claude }, load: { provider in
            loads.append(provider); return ["model": "claude-sonnet-4-6", "apiKey": "synthetic-claude", "baseURL": "https://fixture.test/v1"]
        }, generation: { 4 }, bypass: { false })
        check(selectedCalls == 1 && loads == [.claude] && captured.credential == "synthetic-claude" && captured.generation == 4, "atomic selection and matching credentials")
        var epochReads = 0
        rejects("configuration changed during snapshot") {
            _ = try SelectedLLMConfigurationResolver.snapshot(selected: { .openai }, load: { _ in ["model": "gpt-4.1", "apiKey": "synthetic"] }, generation: { epochReads += 1; return UInt64(epochReads) }, bypass: { false })
        }
        let settings = TranslationSettings()
        let before = settings.generation
        settings.configurationChanged()
        check(settings.generation == before &+ 1, "configuration change bumps the epoch")
        let admission = TranslationAdmission()
        guard let first = admission.acquire(), let second = admission.acquire() else { preconditionFailure("two admissions") }
        check(admission.acquire() == nil && admission.count == 2, "global two, no queue")
        admission.release(first); check(admission.count == 1, "release frees a slot")
        admission.release(second); admission.release(second); check(admission.count == 0, "double release is idempotent")
        check(ComposerTranslationDirection.target(for: "Hello there") == .simplifiedChinese && ComposerTranslationDirection.target(for: "今天讨论设计") == .english, "composer direction follows the draft script")
        print("PASS configuration snapshot, epoch, admission and composer direction")
    }
    @MainActor static func main() async throws {
        try protection()
        try await protocols()
        try configuration()
        print("PASS all translation regression groups (offline production source)")
    }
}
#endif
