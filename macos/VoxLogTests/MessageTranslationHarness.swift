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

@MainActor final class CompletionGate {
    var pending: [CheckedContinuation<TranslationCompletion, Error>] = []
    var calls = 0
    var configurations: [TranslationConfiguration] = []
    func complete(_ source: String, _ config: TranslationConfiguration) async throws -> TranslationCompletion {
        calls += 1; configurations.append(config)
        return try await withCheckedThrowingContinuation { pending.append($0) }
    }
    func finish(_ text: String = "译文") {
        pending.removeFirst().resume(returning: TranslationCompletion(text: text, finishReason: "stop", complete: true))
    }
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
    @MainActor static func stores() async throws {
        let defaults = UserDefaults(suiteName: "message-translation-harness-\(UUID())")!
        let settings = TranslationSettings(defaults: defaults)
        check(!settings.enabled, "development default off")
        settings.setEnabled(true)
        var selectedCalls = 0, loads: [LLMProvider] = []
        let captured = try SelectedLLMConfigurationResolver.snapshot(selected: { selectedCalls += 1; return .claude }, load: { provider in
            loads.append(provider); return ["model": "claude-sonnet-4-6", "apiKey": "synthetic-claude", "baseURL": "https://fixture.test/v1"]
        }, generation: { 4 }, bypass: { false })
        check(selectedCalls == 1 && loads == [.claude] && captured.credential == "synthetic-claude" && captured.generation == 4, "atomic selection and matching credentials")
        let gate = CompletionGate(), admission = TranslationAdmission()
        let service = MessageTranslationService(snapshot: { try config(generation: settings.generation) }, completion: gate.complete)
        let store = MessageTranslationStore(service: service, admission: admission, settings: settings)
        let otherWindow = MessageTranslationStore(service: service, admission: admission, settings: settings)
        store.setVisible(true); otherWindow.setVisible(true)
        var source = TranslationSource(kind: .local, id: "one", scope: "window/session1", text: "original", allowed: true)
        store.translate(current: { source }); store.translate(current: { source })
        otherWindow.translate(current: { TranslationSource(kind: .agent, id: "two", scope: "w2", text: "agent", allowed: true) })
        store.translate(current: { TranslationSource(kind: .local, id: "three", scope: "w1", text: "third", allowed: true) })
        await wait { gate.pending.count == 2 }
        check(gate.calls == 2 && admission.count == 2 && store.state("local:three") == .failure(.busy), "global two, no queue, double-click coalescing")
        source = TranslationSource(kind: .local, id: "one", scope: "window/session1", text: "edited", allowed: true)
        store.reconcile()
        check(store.state("local:one") == nil, "synchronous edit invalidation")
        gate.finish(); gate.finish()
        await wait { admission.count == 0 }
        check(store.state("local:one") == nil, "late result rejected")
        store.translate(current: { source }); await wait { !gate.pending.isEmpty }
        gate.finish("译文"); await wait { admission.count == 0 }
        let calls = gate.calls
        store.collapse(source.key); store.translate(current: { source })
        check(gate.calls == calls, "collapse and expand do not charge")
        var copied: [String] = []
        store.copyOriginal(current: { source }, copy: { copied.append($0) })
        store.copyResult(source.key, copy: { copied.append($0) })
        check(copied == ["edited", "译文"], "explicit independent copy")
        store.translate(current: { source }, retry: true); await wait { !gate.pending.isEmpty }
        settings.configurationChanged()
        check(store.count == 0 && otherWindow.count == 0, "credential/config change clears synchronously")
        gate.finish(); await wait { admission.count == 0 }
        check(store.count == 0, "old config late result rejected")
        store.translate(current: { source }); await wait { !gate.pending.isEmpty }
        store.cancel(source.key)
        check(store.state(source.key) == nil, "cancel immediately clears UI")
        gate.finish(); await wait { admission.count == 0 }
        let forbidden = TranslationSource(kind: .feishu, id: "other", scope: "policy", text: "private", allowed: false)
        store.translate(current: { forbidden })
        check(store.state(forbidden.key) == .failure(.denied), "policy before credential/network")
        let huge = TranslationSource(kind: .local, id: "huge", scope: "local", text: String(repeating: "中", count: 30_000), allowed: true)
        store.translate(current: { huge })
        check(store.state(huge.key) == .failure(.inputLimit) && store.bytes < 1_000, "explicit entire-source input bound")
        let code = TranslationSource(kind: .local, id: "code", scope: "local", text: "```\nx\n```", allowed: true)
        store.translate(current: { code }); check(store.state(code.key) == .onlyCode, "pure code avoids request")
        for i in 0..<150 {
            let s = TranslationSource(kind: .local, id: "code\(i)", scope: "local", text: "`x`", allowed: true)
            store.translate(current: { s })
        }
        check(store.count == 100 && store.bytes <= 4 * 1024 * 1024, "bounded states including failures")
        store.setVisible(false); check(store.count == 0, "window hidden clears")
        store.setVisible(true)
        store.translate(current: { source }); await wait { !gate.pending.isEmpty }
        settings.setEnabled(false); check(store.count == 0, "disable clears synchronously")
        gate.finish(); await wait { admission.count == 0 }
        check(store.count == 0, "disable rejects old result")
        settings.setEnabled(true)
        store.translate(current: { source }); await wait { !gate.pending.isEmpty }
        store.cancel(source.key)
        store.translate(current: { source }); await wait { gate.pending.count == 2 }
        gate.finish("old")
        await wait { admission.count == 1 }
        check(store.state(source.key) == .running, "old request cannot erase new request ownership")
        gate.finish("new"); await wait { admission.count == 0 }
        check(store.state(source.key) == .result("new", expanded: true, identical: false), "new request owns result")
        let bounded = MessageTranslationStore(service: MessageTranslationService(snapshot: { try config() }, completion: { _, _ in
            TranslationCompletion(text: String(repeating: "文", count: 21_800), finishReason: "stop", complete: true)
        }), admission: admission, settings: settings)
        bounded.setVisible(true)
        for i in 0..<90 {
            let row = TranslationSource(kind: .local, id: "large\(i)", scope: "cache", text: "test", allowed: true)
            bounded.translate(current: { row })
            await wait { admission.count == 0 }
            check(bounded.bytes <= 4 * 1024 * 1024 && bounded.count <= 100, "actual 4 MiB result-cache bound")
        }
        check(bounded.count < 90, "byte pressure evicts entries")
        var epochReads = 0
        rejects("configuration changed during snapshot") {
            _ = try SelectedLLMConfigurationResolver.snapshot(selected: { .openai }, load: { _ in ["model": "gpt-4.1", "apiKey": "synthetic"] }, generation: { epochReads += 1; return UInt64(epochReads) }, bypass: { false })
        }
        print("PASS configuration, concurrency, races, explicit copy, bounds, lifecycle")
    }
    @MainActor static func sourceOwners() async throws {
        let settings = TranslationSettings(defaults: UserDefaults(suiteName: "translation-owner-\(UUID())")!)
        settings.setEnabled(true)
        let gate = CompletionGate(), admission = TranslationAdmission()
        let store = MessageTranslationStore(service: MessageTranslationService(snapshot: { try config() }, completion: gate.complete), admission: admission, settings: settings)
        store.setVisible(true)
        let dataset = LocalTranslationDataset(); dataset.translations = store
        let local = VoiceMessage(id: "visible", text: "visible final", rawText: "raw must not leave", agent: "a", createdAt: Date(), durationSeconds: 0, latencyMs: 0, asrProvider: nil, isMe: true)
        dataset.replace([local])
        check(dataset.source(local: local.id)?.text == "visible final", "visible final not raw ASR")
        let agent = AgentChatMessage(taskId: "active", agentId: "a", type: .text, content: "complete immutable event", seq: 0, timestamp: Date())
        let agent2 = AgentChatMessage(taskId: "active", agentId: "a", type: .text, content: "another event", seq: 0, timestamp: Date())
        let tool = AgentChatMessage(taskId: "active", agentId: "a", type: .toolResult, content: "private tool", seq: 0, timestamp: Date())
        dataset.agentMessages = [agent, agent2, tool]
        check(dataset.source(agent: agent.id)?.key != dataset.source(agent: agent2.id)?.key, "UUID not task/seq source identity")
        check(dataset.source(agent: tool.id) == nil, "tools never eligible")
        store.translate(current: { [weak dataset] in dataset?.source(local: local.id) })
        await wait { !gate.pending.isEmpty }
        dataset.refresh([local]); check(store.count == 1, "unchanged refresh preserves running request")
        dataset.replace([local]); check(store.count == 0, "same IDs in rebuilt dataset invalidate synchronously")
        gate.finish(); await wait { admission.count == 0 }
        store.translate(current: { [weak dataset] in dataset?.source(agent: agent.id) })
        await wait { !gate.pending.isEmpty }
        dataset.agentMessages.append(AgentChatMessage(taskId: "active", agentId: "a", type: .status, content: "done", seq: 0, timestamp: Date()))
        check(store.count == 1, "new event does not mutate immutable source")
        dataset.agentMessages = []; check(store.count == 0, "removed Agent events invalidate")
        gate.finish(); await wait { admission.count == 0 }

        var pageText: String? = "authorized text", revision = String(repeating: "a", count: 64)
        var mode = "shared_policy", allowed: Bool? = nil, deleted = false
        var readCalls = 0, writeCalls = 0
        let outbox = FeishuConversationOutbox(persistence: FeishuOutboxPersistence(load: { _ in
            [FeishuPendingText(eventId: "own", conversationId: "fc_dm", text: "own visible original", status: "pending", accepted: false, messageId: nil, attempted: false)]
        }, save: { _, _ in writeCalls += 1; preconditionFailure("translation persisted outbox") }))
        let effects = FeishuAccountEffects(send: { request in
            guard request.httpMethod == "GET" else { writeCalls += 1; preconditionFailure("translation sent a message") }
            if request.url!.lastPathComponent == "me" {
                return (Data(#"{"profile":{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice"},"targetIds":[]}"#.utf8), 200)
            }
            guard request.url!.path == "/v1/conversations/fc_dm/messages" else { preconditionFailure("unexpected message fetch") }
            readCalls += 1
            var item: [String: Any] = ["messageId": "om_one", "eventId": NSNull(), "source": "feishu", "text": pageText as Any? ?? NSNull(), "createdAt": "1000", "updatedAt": "1000", "deleted": deleted, "updated": false, "placeholder": false, "authorKind": "feishu_user", "authorId": "ou_other", "authorName": NSNull(), "status": NSNull()]
            if let allowed { item["modelTranslationAllowed"] = allowed }
            return (try JSONSerialization.data(withJSONObject: ["conversationId": "fc_dm", "mode": mode, "policyRevision": revision, "items": [item], "nextCursor": NSNull(), "hasMore": false]), 200)
        }, load: { try! InputBackupClient.bindCredential("vox_sso_synthetic", to: URL(string: "https://fixture.test")!) }, save: { _ in preconditionFailure("credential write") }, delete: { true }, pauseBackup: {}, resetLogin: {}, completeLogin: { _ in }, persistEndpoint: { _ in }, browser: { _, _ in preconditionFailure("browser") })
        let nav = FeishuConversationStore()
        let account = FeishuAccountClient(conversations: nav, outbox: outbox, endpoint: "https://fixture.test", effects: effects)
        let timeline = account.timeline
        TranslationSourceLifecycle.connect(store, timeline: timeline, outbox: outbox)
        await account.restore(); timeline.select("fc_dm"); timeline.setVisible(true)
        await timeline.refresh(using: account)
        check(timeline.translationSource("m:om_one")?.allowed == false, "old server shared default deny")
        check(timeline.translationSource("m:om_forged") == nil, "unissued message cannot translate")
        outbox.select(FeishuConversation(id: "fc_dm", kind: "personal", name: "mirror", capability: "text_send"))
        check(outbox.translationSource("own", timeline: timeline)?.text == "own visible original", "own original adapter uses visible text")
        store.translate(current: { [weak outbox, weak timeline] in
            guard let timeline else { return nil }; return outbox?.translationSource("own", timeline: timeline)
        })
        await wait { !gate.pending.isEmpty }
        gate.finish(); await wait { admission.count == 0 }
        var ownCopy = ""
        store.copyResult("original:own", copy: { ownCopy = $0 })
        check(ownCopy == "译文" && writeCalls == 0, "own original translate and copy have no persistence/send")
        outbox.select(nil); check(store.count == 0, "outbox selection invalidates synchronously")
        allowed = true; await timeline.refresh(using: account)
        store.translate(current: { [weak timeline] in timeline?.translationSource("m:om_one") })
        await wait { !gate.pending.isEmpty }
        let before = readCalls
        gate.finish(); await wait { admission.count == 0 }
        store.collapse("feishu:m:om_one")
        store.translate(current: { [weak timeline] in timeline?.translationSource("m:om_one") })
        store.copyResult("feishu:m:om_one", copy: { _ in })
        check(readCalls == before && writeCalls == 0, "translate/expand/copy have no Feishu fetch/send/persistence effects")
        pageText = "edit"; await timeline.refresh(using: account)
        check(store.count == 0, "timeline owner edit clears derived result")
        store.translate(current: { [weak timeline] in timeline?.translationSource("m:om_one") })
        await wait { !gate.pending.isEmpty }
        revision = String(repeating: "b", count: 64); allowed = false
        await timeline.refresh(using: account)
        check(store.count == 0, "policy revision invalidates in-flight result")
        gate.finish(); await wait { admission.count == 0 }
        mode = "personal"; allowed = nil
        await timeline.refresh(using: account)
        check(timeline.translationSource("m:om_one")?.allowed == true, "old server bound bot DM allowed")
        store.translate(current: { [weak timeline] in timeline?.translationSource("m:om_one") })
        await wait { !gate.pending.isEmpty }
        deleted = true; pageText = nil; await timeline.refresh(using: account)
        check(store.count == 0 && timeline.translationSource("m:om_one") == nil, "withdrawal cannot be restored by cache")
        gate.finish(); await wait { admission.count == 0 }
        timeline.invalidate(); timeline.bind(account.conversationIdentity); timeline.select("fc_dm")
        deleted = false; pageText = "new login same ID"
        await timeline.refresh(using: account)
        store.translate(current: { [weak timeline] in timeline?.translationSource("m:om_one") })
        await wait { !gate.pending.isEmpty }
        account.endpoint = "https://other.test"
        check(store.count == 0, "account origin clears before awaits")
        gate.finish(); await wait { admission.count == 0 }
        check(writeCalls == 0, "source-owner workflow never persists/sends")
        print("PASS production local/Agent/Feishu owner invalidation and effect isolation")
    }
    @MainActor static func main() async throws {
        try protection()
        try await protocols()
        try await stores()
        try await sourceOwners()
        print("PASS all message translation regression groups (offline production source)")
    }
}
#endif
