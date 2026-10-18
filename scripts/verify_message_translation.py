"""Offline production Swift regression harness for the translation protocol, transport,
protection, configuration snapshot and composer direction; never launches UI or reads credentials."""

import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = (args.output or root / "build/translation-loop/native").resolve()
out.mkdir(parents=True, exist_ok=True)
(out / "stubs.swift").write_text("""import Foundation
func L(_ zh: String, _ en: String) -> String { en }
enum KeychainService {
 static var selectedLLMProvider: LLMProvider { fatalError("real selection read") }
 static func withLLMConfigurationLock<T>(_ body: () throws -> T) rethrows -> T { try body() }
 static func loadLLMCredentials(for p: LLMProvider) -> [String: String]? { fatalError("real credential read") }
 static func llmDisplayConfiguration(for p: LLMProvider) -> (model: String, endpoint: String) { ("synthetic", "https://fixture.test/v1") }
 static func load(key: String) -> String? { fatalError("real credential read") }
 static func loadScalarWithoutInteraction(key: String) -> String? { fatalError("real credential read") }
 static func save(key: String, value: String) throws { fatalError("real credential write") }
 static func delete(key: String) -> Bool { fatalError("real credential delete") }
}
""")
sources = [
    "LLM/LLMProvider.swift",
    "ASR/SpeechRecognizer.swift",
    "ASR/ASRProvider.swift",
]
subprocess.run(
    [
        "swiftc",
        "-D",
        "MESSAGE_TRANSLATION_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        *[str(root / "macos/VoxLog" / p) for p in sources],
        *map(str, sorted((root / "macos/VoxLog/Translation").glob("*.swift"))),
        str(root / "macos/VoxLogTests/MessageTranslationHarness.swift"),
        str(out / "stubs.swift"),
        "-o",
        str(out / "translation-tests"),
    ],
    check=True,
    timeout=120,
)
subprocess.run([str(out / "translation-tests")], check=True, timeout=45)
