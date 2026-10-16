import XCTest
@testable import VoxLog

final class EngineCoordinatorTests: XCTestCase {
    func testEngineReadinessRequiresHealthyOKResponse() throws {
        let url = try XCTUnwrap(URL(string: "http://127.0.0.1:18900/health"))
        let response = try XCTUnwrap(HTTPURLResponse(
            url: url,
            statusCode: 200,
            httpVersion: nil,
            headerFields: nil
        ))
        let unhealthy = try XCTUnwrap(HTTPURLResponse(
            url: url,
            statusCode: 503,
            httpVersion: nil,
            headerFields: nil
        ))

        XCTAssertTrue(EngineCoordinator.isHealthyResponse(
            data: Data(#"{"status":"ok"}"#.utf8),
            response: response,
            error: nil
        ))
        XCTAssertFalse(EngineCoordinator.isHealthyResponse(
            data: Data(#"{"status":"starting"}"#.utf8),
            response: response,
            error: nil
        ))
        XCTAssertFalse(EngineCoordinator.isHealthyResponse(
            data: Data(#"{"status":"ok"}"#.utf8),
            response: unhealthy,
            error: nil
        ))
    }

    func testCoordinatorWaitsForHealthInsteadOfAssumingFixedStartupDelay() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/Services/EngineCoordinator.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("waitForReadiness(process: proc)"))
        XCTAssertTrue(source.contains("maxReadinessAttempts"))
        XCTAssertFalse(source.contains(".now() + 1.5"))
    }

    func testEngineDirectoryResolverFindsCurrentRepositoryCandidate() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let engine = root.appendingPathComponent("engine", isDirectory: true)
        try FileManager.default.createDirectory(at: engine, withIntermediateDirectories: true)
        try Data().write(to: engine.appendingPathComponent("__init__.py"))
        defer { try? FileManager.default.removeItem(at: root) }

        XCTAssertEqual(
            EngineCoordinator.resolveEngineWorkingDirectory(candidates: [root]),
            root
        )
    }

    func testBundledEngineResolverRequiresExecutableBinary() throws {
        let resources = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let binary = resources.appendingPathComponent("bin/voxagent-engine")
        try FileManager.default.createDirectory(
            at: binary.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data("engine".utf8).write(to: binary)
        defer { try? FileManager.default.removeItem(at: resources) }

        XCTAssertNil(EngineCoordinator.resolveBundledEngineExecutable(resourceURL: resources))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: binary.path
        )
        XCTAssertEqual(
            EngineCoordinator.resolveBundledEngineExecutable(resourceURL: resources),
            binary
        )
    }

    func testEngineEnvironmentBridgesKeychainLLMConfigToTranslationProvider() {
        let environment = EngineCoordinator.engineEnvironment(
            base: ["PATH": "/usr/bin"],
            translationConfig: LLMConfig(
                apiKey: "test-key-never-logged",
                model: "test-model",
                baseURL: "https://example.invalid/v1"
            )
        )

        XCTAssertEqual(environment["PATH"], "/usr/bin")
        XCTAssertEqual(environment["VOXAGENT_TRANSLATION_API_KEY"], "test-key-never-logged")
        XCTAssertEqual(environment["VOXAGENT_TRANSLATION_MODEL"], "test-model")
        XCTAssertEqual(
            environment["VOXAGENT_TRANSLATION_BASE_URL"],
            "https://example.invalid/v1"
        )
    }

    func testEngineFailureSummaryIsBoundedAndRedactsCredentialLikeValues() {
        let stderr = """
        provider failed Authorization: Bearer secret-token
        VOXAGENT_TRANSLATION_API_KEY=another-secret
        final diagnostic line
        """

        let summary = EngineCoordinator.safeEngineFailureSummary(
            Data(stderr.utf8),
            limit: 96
        )

        XCTAssertLessThanOrEqual(summary.count, 96)
        XCTAssertFalse(summary.contains("secret-token"))
        XCTAssertFalse(summary.contains("another-secret"))
        XCTAssertTrue(summary.contains("[REDACTED]"))
        XCTAssertTrue(summary.contains("final diagnostic line"))
    }

    func testEngineCoordinatorContinuouslyDrainsStderr() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/Services/EngineCoordinator.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("readabilityHandler"))
        XCTAssertTrue(source.contains("safeEngineFailureSummary"))
    }
}
