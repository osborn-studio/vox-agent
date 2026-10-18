"""Offline production Swift outbox harness, with injected storage/network effects."""

import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.output or root / "build/phase4-loop/native"
out.mkdir(parents=True, exist_ok=True)
subprocess.run(
    [
        "swiftc",
        "-D",
        "FEISHU_SENDING_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        str(root / "macos/VoxLog/UI/Chat/SidebarSelection.swift"),
        str(root / "macos/VoxLog/Services/FeishuConversationOutbox.swift"),
        str(root / "macos/VoxLogTests/FeishuConversationSendingHarness.swift"),
        "-o",
        str(out / "sending-tests"),
    ],
    check=True,
    timeout=90,
)
# Each run gets an isolated persistent directory; never load app defaults/data.
with tempfile.TemporaryDirectory(dir=out) as directory:
    subprocess.run([str(out / "sending-tests"), directory], check=True, timeout=30)

# Also compile the real account transport and text UI. Stubs fail if any live
# credential/browser/login effect is reached; all lifecycle effects are injected.
stubs = out / "account-stubs.swift"
stubs.write_text("""import Foundation
func L(_ zh: String, _ en: String) -> String { en }
enum KeychainService {
 static func load(key: String) -> String? { fatalError("real credential read") }
 static func loadScalarWithoutInteraction(key: String) -> String? { fatalError("real credential read") }
 static func save(key: String, value: String) throws { fatalError("real credential write") }
 static func delete(key: String) -> Bool { fatalError("real credential delete") }
}
@MainActor enum LoginSession {
 static func resetLogin() { fatalError("uninjected login reset") }
 static func completeFeishuLogin(orgID: String, name: String) { fatalError("uninjected login completion") }
}
enum TranslationTarget { case simplifiedChinese, english; var label: String { "" } }
enum ComposerTranslationDirection { static func target(for text: String) -> TranslationTarget { .simplifiedChinese } }
struct ComposerTranslationResult { let text: String; let target: TranslationTarget; let unchanged: Bool }
@MainActor enum ComposerTranslator {
 static func translate(_ draft: String) async throws -> ComposerTranslationResult { fatalError("real model call") }
}
enum TranslationFailure: Error { case network, cancelled; var message: String { "" } }
""")
sources = [
    "Services/InputBackupClient.swift",
    "Services/FeishuAccountClient.swift",
    "Services/FeishuTimelineModel.swift",
    "Services/FeishuConversationStore.swift",
    "Services/FeishuConversationOutbox.swift",
    "UI/Chat/SidebarSelection.swift",
    "UI/Chat/FeishuTextComposer.swift",
    "UI/Chat/ComposerTranslateControls.swift",
    "UI/Chat/CopyOriginalButton.swift",
    "UI/Chat/ChatLayoutMetrics.swift",
    "UI/DesignSystem.swift",
]
subprocess.run(
    [
        "swiftc",
        "-D",
        "FEISHU_SENDING_ACCOUNT_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        *[str(root / "macos/VoxLog" / source) for source in sources],
        str(stubs),
        str(root / "macos/VoxLogTests/FeishuSendingAccountHarness.swift"),
        "-o",
        str(out / "sending-account-tests"),
    ],
    check=True,
    timeout=90,
)
with tempfile.TemporaryDirectory(dir=out) as directory:
    subprocess.run([str(out / "sending-account-tests"), directory], check=True, timeout=30)

# Repair regressions use the same production outbox with injected effects.
subprocess.run([
    "swiftc", "-D", "PHASE4_REPAIR_HARNESS", "-sdk", args.sdk,
    "-parse-as-library", "-module-cache-path", str(out / "module-cache"),
    str(root / "macos/VoxLog/UI/Chat/SidebarSelection.swift"),
    str(root / "macos/VoxLog/Services/FeishuConversationOutbox.swift"),
    str(root / "macos/VoxLogTests/FeishuConversationSendingRepairHarness.swift"),
    "-o", str(out / "sending-repair-tests"),
], check=True, timeout=90)
with tempfile.TemporaryDirectory(dir=out) as directory:
    subprocess.run([str(out / "sending-repair-tests"), directory], check=True, timeout=30)
