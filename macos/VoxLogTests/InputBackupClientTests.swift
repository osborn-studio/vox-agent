import XCTest
@testable import VoxLog

@MainActor
final class InputBackupClientTests: XCTestCase {
    private func directory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    func testQueueSurvivesRestartAndOnlyAcknowledgedEventIsRemoved() async throws {
        let dir = try directory()
        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        let first = InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("test-token", to: config.endpoint) })
        try first.enqueue(eventID: "event-1", text: "我想打通 Vox Agent 和飞书。", configuration: config)
        let restored = InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("test-token", to: config.endpoint) }, send: { request in
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
            XCTAssertEqual(body["eventId"] as? String, "event-1")
            return (Data(#"{"eventId":"event-1","status":"pending","messageId":null}"#.utf8), 202)
        })
        XCTAssertEqual(restored.pendingCount, 1)
        await restored.flush(configuration: config)
        XCTAssertEqual(restored.pendingCount, 0)
        XCTAssertEqual(InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("test-token", to: config.endpoint) }).pendingCount, 0)
    }

    func testWrongEventAndChangedCredentialDoNotDiscardOrResend() async throws {
        let dir = try directory()
        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        let queue = InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("first-token", to: config.endpoint) }, send: { _ in
            (Data(#"{"eventId":"wrong","status":"pending"}"#.utf8), 202)
        })
        try queue.enqueue(eventID: "event-1", text: "hello", configuration: config)
        await queue.flush(configuration: config)
        XCTAssertEqual(queue.pendingCount, 1)
        let changed = InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("different-token", to: config.endpoint) }, send: { _ in
            XCTFail("A new account must not receive a previous account's queue")
            return (Data(), 500)
        })
        await changed.flush(configuration: config)
        XCTAssertEqual(changed.pendingCount, 1)
    }

    func testDisabledServiceRetainsQueueAndExplainsServerAction() async throws {
        let dir = try directory()
        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        let queue = InputBackupClient(directory: dir, credential: {
            try! InputBackupClient.bindCredential("test-token", to: config.endpoint)
        }, send: { _ in
            (Data(#"{"detail":"BACKUP_DISABLED"}"#.utf8), 503)
        })
        try queue.enqueue(eventID: "disabled-event", text: "test", configuration: config)
        await queue.flush(configuration: config)
        XCTAssertEqual(queue.pendingCount, 1)
        XCTAssertEqual(queue.statusText, L("云端备份未启用，记录已保留；请管理员启用投递服务", "Server backup is disabled; records retained. Ask your administrator to enable delivery"))
    }

    func testFailureReasonsRetainQueueWithoutDisplayingServerContent() async throws {
        let cases: [(Int, String, String)] = [
            (403, #"{"detail":"TARGET_DENIED"}"#, L("备份目标未配置或未授权，记录已保留；请管理员核对目标 ID 和账号权限", "Backup destination is not configured or authorized; records retained. Ask your administrator to check the target ID and account access")),
            (401, #"{"detail":"UNAUTHORIZED"}"#, L("备份登录已失效，记录已保留；请重新登录飞书", "Backup sign-in is no longer valid; records retained. Sign in to Feishu again")),
            (502, "private upstream error", L("备份未确认，记录已保留；请检查凭据、目标或大小限制", "Backup unconfirmed; retained locally. Check credentials, target and size limit")),
            (403, #"{"detail":"BACKUP_DISABLED"}"#, L("备份未确认，记录已保留；请检查凭据、目标或大小限制", "Backup unconfirmed; retained locally. Check credentials, target and size limit"))
        ]
        for (code, body, expected) in cases {
            let dir = try directory()
            let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
            let queue = InputBackupClient(directory: dir, credential: {
                try! InputBackupClient.bindCredential("test-token", to: config.endpoint)
            }, send: { _ in (Data(body.utf8), code) })
            try queue.enqueue(eventID: "failure-event", text: "test", configuration: config)
            await queue.flush(configuration: config)
            XCTAssertEqual(queue.statusText, expected)
            XCTAssertEqual(queue.pendingCount, 1)
            XCTAssertEqual(InputBackupClient(directory: dir, credential: { nil }).pendingCount, 1)
        }
    }

    // Why records stay "Saved locally": every pending row shows this reason.
    func testHoldReasonExplainsWhyTheQueueIsNotDraining() async throws {
        let cases: [(Int, String, InputBackupHoldReason)] = [
            (401, #"{"detail":"UNAUTHORIZED"}"#, .signInExpired),
            (403, #"{"detail":"TARGET_DENIED"}"#, .targetDenied),
            (503, #"{"detail":"BACKUP_DISABLED"}"#, .backupDisabled),
            (502, "private upstream error", .serverUnconfirmed(502)),
        ]
        for (code, body, expected) in cases {
            let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
            let queue = InputBackupClient(directory: try directory(), credential: {
                try! InputBackupClient.bindCredential("test-token", to: config.endpoint)
            }, send: { _ in (Data(body.utf8), code) })
            try queue.configure(config) // deliveryState reads the active configuration
            try queue.enqueue(eventID: "held-event", text: "test", configuration: config)
            XCTAssertNil(queue.holdReason, "nothing tried yet")
            await queue.flush(configuration: config)
            XCTAssertEqual(queue.holdReason, expected, "\(code)")
            XCTAssertNotNil(queue.lastAttemptAt)
            XCTAssertEqual(queue.deliveryState(eventID: "held-event"), .local)
            XCTAssertFalse(expected.label.contains("private upstream"), "server text is never shown")
        }
    }

    func testUnreachableServiceMissingCredentialAndFailedRenewalAreNamed() async throws {
        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        let offline = InputBackupClient(directory: try directory(), credential: {
            try! InputBackupClient.bindCredential("test-token", to: config.endpoint)
        }, send: { _ in throw URLError(.cannotConnectToHost) })
        try offline.enqueue(eventID: "offline-event", text: "test", configuration: config)
        await offline.flush(configuration: config)
        XCTAssertEqual(offline.holdReason, .unreachable)

        var stored: String? = try InputBackupClient.bindCredential("test-token", to: config.endpoint)
        let missing = InputBackupClient(directory: try directory(), credential: { stored }, send: { _ in
            XCTFail("no request without a credential")
            return (Data(), 500)
        })
        try missing.enqueue(eventID: "missing-event", text: "test", configuration: config)
        stored = nil
        await missing.flush(configuration: config)
        XCTAssertEqual(missing.holdReason, .credentialMissing)

        let renewal = InputBackupClient(directory: try directory(), credential: {
            try! InputBackupClient.bindCredential("vox_sso_expired", to: config.endpoint)
        }, send: { _ in
            XCTFail("no request after a failed renewal")
            return (Data(), 500)
        })
        renewal.ensureCredential = { false }
        try renewal.enqueue(eventID: "renewal-event", text: "test", configuration: config)
        await renewal.flush(configuration: config)
        XCTAssertEqual(renewal.holdReason, .renewalFailed, "a failed renewal is no longer silent")
    }

    func testASuccessfulSendClearsTheHoldReason() async throws {
        let config = try InputBackupConfiguration(endpoint: "https://backup.example.test", targetID: "archive")
        var code = 401
        let queue = InputBackupClient(directory: try directory(), credential: {
            try! InputBackupClient.bindCredential("test-token", to: config.endpoint)
        }, send: { request in
            let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
            let id = body["eventId"] as! String
            return code == 202 ? (Data("{\"eventId\":\"\(id)\",\"status\":\"pending\"}".utf8), 202) : (Data(), code)
        })
        try queue.enqueue(eventID: "event-a", text: "a", configuration: config)
        await queue.flush(configuration: config)
        XCTAssertEqual(queue.holdReason, .signInExpired)
        code = 202
        await queue.flush(configuration: config)
        XCTAssertNil(queue.holdReason)
        XCTAssertEqual(queue.pendingCount, 0)
    }

    func testEveryHoldReasonHasBilingualTextWithAnAction() {
        let all: [InputBackupHoldReason] = [.signInExpired, .renewalFailed, .credentialMissing, .targetDenied, .backupDisabled,
                                            .serverUnconfirmed(500), .unreachable, .queueFull, .localStorage, .awaitingCheck, .deliveryBlocked]
        for reason in all {
            XCTAssertTrue(reason.label.contains(" · "), "\(reason) names what to do")
        }
    }

    func testRejectsRemotePlaintextAndURLCredentials() {
        XCTAssertThrowsError(try InputBackupConfiguration(endpoint: "http://remote.test", targetID: "archive"))
        XCTAssertThrowsError(try InputBackupConfiguration(endpoint: "https://user:secret@example.test", targetID: "archive"))
        XCTAssertThrowsError(try InputBackupConfiguration(endpoint: "https://example.test?token=secret", targetID: "archive"))
        XCTAssertNoThrow(try InputBackupConfiguration(endpoint: "http://127.0.0.1:8766", targetID: "archive"))
    }

    func testCorruptQueueIsNotOverwritten() throws {
        let dir = try directory()
        let file = dir.appendingPathComponent("pending.json")
        let corrupt = Data("broken".utf8)
        try corrupt.write(to: file)
        let config = try InputBackupConfiguration(endpoint: "https://example.test", targetID: "archive")
        let queue = InputBackupClient(directory: dir, credential: { try! InputBackupClient.bindCredential("unused", to: config.endpoint) })
        XCTAssertThrowsError(try queue.enqueue(eventID: "event-1", text: "hello", configuration: config))
        XCTAssertEqual(try Data(contentsOf: file), corrupt)
    }
}
