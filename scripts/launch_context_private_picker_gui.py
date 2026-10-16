#!/usr/bin/env python3
"""Launch isolated production private-inbox GUI with synthetic GET responses and temp stores.

No network, real Keychain, employee content or live mutation. Close fixture after QA.
"""

from pathlib import Path
import sys
import subprocess
import plistlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_input_backup_client import STUB

root = Path(__file__).resolve().parents[1]
work = root / "build/private-picker-gui"
work.mkdir(parents=True, exist_ok=True)
(work / "Dependencies.swift").write_text(
    STUB.replace(
        "static func load(key:",
        "static func addSecureScalarIfAbsent(key: String, value: String) throws {}\n static func loadSecureScalarStrict(key: String) throws -> String? { nil }\n static func load(key:",
    )
)
fixture_source = r"""
import SwiftUI
import AppKit
import Observation
@MainActor @Observable final class FixtureAccount { var owner = String(repeating: "f", count: 64) }
@main struct Fixture {
 @MainActor static func main() {
  let app = NSApplication.shared; app.setActivationPolicy(.regular)
  let root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("vox-picker-gui-" + UUID().uuidString)
  var inputKey: String?, requestKey: String?, draftKey: String?
  let input = ContextPrivateInputStore(directory: root.appendingPathComponent("Inputs"), readKey: { inputKey }, saveKey: { inputKey = $0 })
  let pending = ContextPrivateRequestStore(directory: root.appendingPathComponent("Requests"), readKey: { requestKey }, saveKey: { requestKey = $0 })
  let draft = ContextPrivateDraftStore(directory: root.appendingPathComponent("Drafts"), readKey: { draftKey }, saveKey: { draftKey = $0 })
  let inbox = ContextPrivateInbox(requestStore: pending, inputStore: input)
  let account = FixtureAccount()
  // ARCHIVE_FIXTURE
  let request: ContextPrivateInbox.Request = { path, method, _ in
   precondition(method == "GET", "GUI fixture never dispatches mutations")
   let value: [String: Any] = path == "v1/context-projects"
    ? ["version":1,"projects":[["id":"synthetic-vox","name":"Synthetic Vox","repositoryId":"synthetic/repo"]]]
    : ["version":1,"allowed":true,"meaning":"current-private-intake-authorization"]
   return try JSONSerialization.data(withJSONObject:value)
  }
  let view = VStack {
   Button("Synthetic switch account") { account.owner = String(repeating: "e", count: 64) }
   ContextPrivateInboxView(store:draft,scope:{ account.owner },request:request,currentScope:{ account.owner },inbox:inbox)
  }
  let window=NSWindow(contentRect:NSRect(x:0,y:0,width:860,height:760),styleMask:[.titled,.closable,.resizable,.miniaturizable],backing:.buffered,defer:false)
  window.title="Vox Private Picker — Synthetic GUI"
  window.contentView=NSHostingView(rootView:view)
  window.center();window.makeKeyAndOrderFront(nil);app.activate(ignoringOtherApps:true);app.run()
 }
}
"""
if "--archive-fixture" in sys.argv:
    fixture_source = fixture_source.replace("// ARCHIVE_FIXTURE", r'''let archive = root.appendingPathComponent("Requests/Archived")
  for directory in [root, root.appendingPathComponent("Requests"), archive] {
   try! FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
  }
  precondition(FileManager.default.createFile(atPath: archive.appendingPathComponent(account.owner + "." + String(repeating: "a", count: 64) + ".v1.aes").path, contents: Data("synthetic opaque archive fixture".utf8), attributes: [.posixPermissions: 0o600]))''')
(work / "Fixture.swift").write_text(fixture_source)
app = work / "Vox Private Picker GUI.app"
(app / "Contents/MacOS").mkdir(parents=True, exist_ok=True)
(app / "Contents/Info.plist").write_bytes(
    plistlib.dumps(
        {
            "CFBundleIdentifier": "com.osborn.voxagent.private-picker-gui",
            "CFBundleExecutable": "picker",
            "CFBundleName": "Vox Private Picker GUI",
            "CFBundlePackageType": "APPL",
        }
    )
)
files = [
    "Services/InputBackupClient.swift",
    "Services/ContextWeeklySources.swift",
    "Services/ContextPrivateInbox.swift",
    "Services/ContextPrivateDraftStore.swift",
    "Services/ContextPrivateRequestStore.swift",
    "Services/ContextPrivateInputStore.swift",
    "UI/DesignSystem.swift",
    "UI/Chat/ChatLayoutMetrics.swift",
    "UI/Chat/ContextPrivateInboxView.swift",
]
subprocess.run(
    [
        "swiftc",
        "-sdk",
        "/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk",
        "-swift-version",
        "5",
        "-parse-as-library",
        *[str(root / "macos/VoxLog" / f) for f in files],
        str(work / "Dependencies.swift"),
        str(work / "Fixture.swift"),
        "-o",
        str(app / "Contents/MacOS/picker"),
    ],
    check=True,
)
subprocess.run(["open", str(app)], check=True)
print(app)
