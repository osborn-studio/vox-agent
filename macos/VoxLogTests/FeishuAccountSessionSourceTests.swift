import XCTest

/// Source-level contract for Feishu session-renewal diagnostics. The account
/// state machine itself runs offline in FeishuAccountHarness.swift via
/// scripts/verify_feishu_account.py; this file guards the view wiring and the
/// "no raw server text" boundary, which SwiftUI builds cannot exercise here.
final class FeishuAccountSessionSourceTests: XCTestCase {

    func testAccountViewRendersExpiryAndLastRenewalAsInlineReadOnlyText() throws {
        let source = try readSource("VoxLog/UI/Login/FeishuAccountView.swift")

        XCTAssertTrue(source.contains("if let validity = account.sessionValidityLine"))
        XCTAssertTrue(source.contains("if let renewal = account.lastRenewalLine"))
        XCTAssertTrue(source.contains(".monospacedDigit()"))
        XCTAssertTrue(source.contains(".accessibilityLabel(validity)"))
        XCTAssertTrue(source.contains(".accessibilityLabel(renewal)"))
        XCTAssertTrue(source.contains("if !account.status.isEmpty { Text(account.status)"))
        // DESIGN.md: status is inline text, never a toast, alert or colour-only cue.
        XCTAssertFalse(source.contains(".alert("))
        XCTAssertFalse(source.contains("Color.red"))
        XCTAssertFalse(source.contains("Color.orange"))
        XCTAssertFalse(source.contains("account.lastRenewal?.outcome"))
    }

    func testClientMapsRenewalFailuresThroughFixedOutcomeAllowlist() throws {
        let source = try readSource("VoxLog/Services/FeishuAccountClient.swift")

        XCTAssertTrue(source.contains("enum FeishuRenewalOutcome: String, Sendable, Equatable"))
        for code in ["MEMBERSHIP_CHECK_UNAVAILABLE", "RATE_LIMITED", "IN_PROGRESS", "SESSION_EXPIRED", "ACCOUNT_DISABLED"] {
            XCTAssertTrue(source.contains("= \"\(code)\""), code)
        }
        XCTAssertTrue(source.contains("case (503, \"MEMBERSHIP_CHECK_UNAVAILABLE\"): return .membershipCheckUnavailable"))
        XCTAssertTrue(source.contains("case (401, \"ACCOUNT_DISABLED\"): return .accountDisabled"))
        XCTAssertTrue(source.contains("private(set) var sessionExpiresAt: Date?"))
        XCTAssertTrue(source.contains("private(set) var lastRenewal: FeishuRenewalReport?"))
        XCTAssertTrue(source.contains("服务端无法确认飞书成员状态（请检查应用通讯录权限）"))
        // Retry on transient outcomes, expiry only on the session-ending ones.
        XCTAssertTrue(source.contains("case .sessionExpired, .accountDisabled, .identityMismatch: return true"))
        XCTAssertTrue(source.contains("retryRenewalAfter = Date().addingTimeInterval(60)"))
        // Status lines are built from the outcome, never from the response body.
        XCTAssertFalse(source.contains("status = String(decoding: data"))
        XCTAssertFalse(source.contains("status = detail"))
    }

    private func readSource(_ relativePath: String) throws -> String {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: packageRoot.appendingPathComponent(relativePath),
            encoding: .utf8
        )
    }
}
