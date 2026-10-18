"""Compile the isolated live translation acceptance runner; calls require explicit --live authorization.

Uses production transport/Keychain config without launching Vox or starting its engine.
Only the committed synthetic fixture is accepted. Never prints credentials or raw errors.
The former --render/--gui timeline runners were removed with the timeline translation
feature; translation now happens only in the composer (ComposerTranslator).
"""

import argparse
import hashlib
import sys
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--sdk", required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--live", action="store_true", required=True)
p.add_argument("--preferences-domain", default="com.osborn.voxagent")
p.add_argument(
    "--compile-only", action="store_true", help="Build without running or reading credentials"
)
a = p.parse_args()
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
    "Translation/ComposerTranslation.swift",
]
runner = "TranslationLiveAcceptance.swift"
exe = out / "live-acceptance"
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
if a.compile_only:
    print("PASS: acceptance runner compiled; execution not requested")
    sys.exit(0)
args = [str(exe), str(out), str(root / "tests/fixtures/message_translation_quality.json"), a.preferences_domain]
try:
    result = subprocess.run(args, timeout=1000)
except subprocess.TimeoutExpired:
    print("Acceptance runner timed out; inspect partial results before any retry.", file=sys.stderr)
    sys.exit(1)
sys.exit(result.returncode)
