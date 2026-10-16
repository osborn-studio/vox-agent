"""Compile isolated acceptance runners; live calls require explicit --live authorization.

Uses production transport/Keychain config without launching Vox or starting its engine.
Only the committed synthetic fixture is accepted. Never prints credentials or raw errors.
"""

import argparse
import hashlib
import plistlib
import sys
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--sdk", required=True)
p.add_argument("--output", type=Path, required=True)
mode = p.add_mutually_exclusive_group(required=True)
mode.add_argument("--live", action="store_true")
mode.add_argument("--render", action="store_true")
mode.add_argument("--gui", action="store_true", help="Build an isolated interactive GUI; never auto-launch")
p.add_argument("--ui-language", choices=("zh", "en"), default="zh", help="Language of isolated GUI/render components")
p.add_argument("--preferences-domain", default="com.osborn.voxagent")
p.add_argument(
    "--compile-only", action="store_true", help="Build without running or reading credentials"
)
a = p.parse_args()
if a.live and a.ui_language != "zh":
    p.error("--ui-language applies only to --gui and --render")
root = Path(__file__).resolve().parents[1]
fixture = root / "tests/fixtures/message_translation_quality.json"
if (
    a.live
    and hashlib.sha256(fixture.read_bytes()).hexdigest()
    != "508c64423abae4112de1814272f972ebc7f3a422d286b6a8e97ae4428ea808f7"
):
    p.error("synthetic fixture changed; review and authorize the new inputs first")
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
stubs = """import Foundation
func L(_ zh: String, _ en: String) -> String { zh }
extension Notification.Name { static let asrProviderDidChange = Notification.Name("acceptance-unused-asr") }
enum ASRIdentityStore { static func loadOrCreateUID() -> String { fatalError("ASR is outside acceptance scope") } }
enum ASRProviderRegistry {
 static func configType(for provider: ASRProvider) -> (any ASRProviderConfig.Type)? { fatalError("ASR is outside acceptance scope") }
}
"""
if a.ui_language == "en":
    stubs = stubs.replace("-> String { zh }", "-> String { en }")
if a.render or a.gui:
    progress = (root / "macos/VoxLog/Services/EngineEventHandler.swift").read_text()
    stubs += progress[
        progress.index("struct TaskProgressInfo {") : progress.index("struct PermissionApproval:")
    ]
    stubs += """enum KeychainService {
 static var selectedLLMProvider: LLMProvider { .openai }
 static func withLLMConfigurationLock<T>(_ action: () throws -> T) rethrows -> T { try action() }
 static func loadLLMCredentials(for provider: LLMProvider) -> [String: String]? { fatalError("no key access in UI fixture") }
 static func llmDisplayConfiguration(for provider: LLMProvider) -> (model: String, endpoint: String) { ("synthetic", "https://fixture.invalid") }
}
"""
(out / "stubs.swift").write_text(stubs)
sources = [
    "Services/KeychainService.swift",
    "LLM/LLMProvider.swift",
    "LLM/LLMProviderRegistry.swift",
    "LLM/Providers/OpenAICompatibleLLMConfig.swift",
    "LLM/Providers/ClaudeLLMConfig.swift",
    "ASR/SpeechRecognizer.swift",
    "ASR/ASRProvider.swift",
    "ASR/Providers/VolcanoASRConfig.swift",
    "Translation/TranslationSource.swift",
    "Translation/TranslationProtection.swift",
    "Translation/SelectedLLMConfigurationResolver.swift",
    "Translation/TranslationTransport.swift",
    "Translation/MessageTranslationStore.swift",
]
if a.render or a.gui:
    sources.remove("Services/KeychainService.swift")
    sources += [
        "UI/DesignSystem.swift",
        "UI/Chat/ChatLayoutMetrics.swift",
        "Translation/MessageTextActions.swift",
        "UI/Chat/ChatArea.swift",
        "UI/Chat/AgentTimelineTurn.swift",
        "Services/VoiceMessage.swift",
        "Services/AgentChatMessage.swift",
        "Agent/AgentModels.swift",
    ]
runner = "TranslationGUIAcceptance.swift" if a.gui else ("TranslationLiveAcceptance.swift" if a.live else "TranslationRenderAcceptance.swift")
exe = out / ("live-acceptance" if a.live else "render-acceptance")
if a.gui:
    contents = out / "Vox Translation GUI.app/Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    exe = contents / "MacOS/Fixture"
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "com.osborn.voxagent.translation-gui",
                      "CFBundleName": "Vox Translation GUI", "CFBundleExecutable": "Fixture",
                      "CFBundlePackageType": "APPL", "NSHighResolutionCapable": True}, stream)
subprocess.run(
    [
        "swiftc",
        "-D",
        "TRANSLATION_ACCEPTANCE",
        "-sdk",
        a.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        *[str(root / "macos/VoxLog" / f) for f in sources],
        str(out / "stubs.swift"),
        str(root / "macos/VoxLogTests" / runner),
        "-o",
        str(exe),
    ],
    check=True,
    timeout=180,
)
if a.compile_only or a.gui:
    print("PASS: acceptance runner compiled; execution not requested")
    sys.exit(0)
args = [str(exe), str(out)]
if a.live:
    args += [str(root / "tests/fixtures/message_translation_quality.json"), a.preferences_domain]
try:
    result = subprocess.run(args, timeout=1000 if a.live else 120)
except subprocess.TimeoutExpired:
    print("Acceptance runner timed out; inspect partial results before any retry.", file=sys.stderr)
    sys.exit(1)
sys.exit(result.returncode)
