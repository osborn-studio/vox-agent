"""Build a synthetic native GUI using production retained-input view/tokens/types.

No client instance, credential access, engine, network or real employee content.
Launch/interact through the supported UI automation tool, not shell UI scripts.
"""
import argparse
import plistlib
import subprocess
from pathlib import Path


SOURCE = '''import SwiftUI
@main struct Fixture: App {
    @State private var showInputs = true
    var body: some Scene {
        WindowGroup("Vox Context Review Fixture") {
            VStack(alignment: .leading, spacing: TF.spacingMD) {
                Text("SYNTHETIC FIXTURE — no network, credentials or employee data")
                    .font(.headline)
                Button(showInputs ? "Empty account fixture" : "Restore synthetic fixture") { showInputs.toggle() }
                RetainedContextInputsView(inputs: showInputs ? [
                    RetainedContextInput(id: "synthetic-unknown", text: String(repeating: "Synthetic context: implement a parser; completion is not verified.\\n", count: 18), project: InputBackupProjectSnapshot(id: "vox", repositoryID: "team/vox"), state: .unknown),
                    RetainedContextInput(id: "synthetic-rejected", text: "Synthetic rejected input. 原文保留，先核对再处理。", project: nil, state: .rejected)
                ] : [], mayCopy: { _ in true })
                Spacer()
            }
            .padding(TF.spacingMD)
            .frame(minWidth: 740, minHeight: 420)
        }
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / "build/context-review-gui"
    bundle = destination / "Vox Context Review Fixture.app"
    executable = bundle / "Contents/MacOS/ContextReviewFixture"
    executable.parent.mkdir(parents=True, exist_ok=True)
    source = destination / "Fixture.swift"
    source.write_text(SOURCE)
    dependencies = destination / "Dependencies.swift"
    dependencies.write_text('import Foundation\nfunc L(_ chinese: String, _ english: String) -> String { english }\nenum KeychainService { static func load(key: String) -> String? { nil } }\n')
    with (bundle / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleName": "Vox Context Review Fixture", "CFBundleIdentifier": "com.osborn.vox.context-review-fixture", "CFBundleExecutable": "ContextReviewFixture", "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "NSHighResolutionCapable": True}, handle)
    subprocess.run(["swiftc", "-sdk", args.sdk, "-swift-version", "5", "-parse-as-library", str(root / "macos/VoxLog/Services/InputBackupClient.swift"), str(root / "macos/VoxLog/UI/DesignSystem.swift"), str(root / "macos/VoxLog/UI/Chat/RetainedContextInputsView.swift"), str(dependencies), str(source), "-o", str(executable)], check=True)
    print(bundle)


if __name__ == "__main__":
    main()
