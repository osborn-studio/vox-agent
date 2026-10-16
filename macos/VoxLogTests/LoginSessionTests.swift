import XCTest
@testable import VoxLog

final class LoginSessionTests: XCTestCase {

    func testCompleteLocalTeamLoginOnlyWritesLoginKeys() {
        let suiteName = "LoginSessionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        defaults.set("existing-language", forKey: "tf_language")
        defaults.set(true, forKey: "tf_hasCompletedSetup")

        LoginSession.completeLocalTeamLogin(in: defaults)

        let session = LoginSession.load(from: defaults)
        XCTAssertTrue(session.hasCompletedLogin)
        XCTAssertEqual(session.mode, .localTeam)
        XCTAssertEqual(session.activeOrgId, LoginSession.defaultOrgId)
        XCTAssertEqual(session.activeOrgName, LoginSession.defaultOrgName)
        XCTAssertEqual(defaults.string(forKey: "tf_language"), "existing-language")
        XCTAssertTrue(defaults.bool(forKey: "tf_hasCompletedSetup"))
    }

    func testResetLoginLeavesOtherSettingsUntouched() {
        let suiteName = "LoginSessionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        defaults.set("zh", forKey: "tf_language")
        LoginSession.completeLocalTeamLogin(in: defaults)

        LoginSession.resetLogin(in: defaults)

        XCTAssertFalse(LoginSession.load(from: defaults).hasCompletedLogin)
        XCTAssertEqual(defaults.string(forKey: "tf_language"), "zh")
    }

}
