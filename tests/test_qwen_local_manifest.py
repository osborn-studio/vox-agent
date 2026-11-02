"""The Windows local Qwen manifest must pin exactly the bytes macOS trusts."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "engine/model_manifests/qwen3-asr-0.6b.json"
SWIFT = ROOT / "macos/VoxLog/Services/QwenModelCatalog.swift"

SWIFT_FILE = re.compile(
    r'path:\s*"(?P<path>[^"]+)",\s*size:\s*(?P<size>\d+),\s*sha256:\s*"(?P<sha>[0-9a-f]{64})"'
)


def swift_catalog() -> tuple[str, dict[str, tuple[int, str]]]:
    source = SWIFT.read_text(encoding="utf-8")
    revision = re.search(r'static let revision = "([0-9a-f]{40})"', source).group(1)
    files = {m["path"]: (int(m["size"]), m["sha"]) for m in SWIFT_FILE.finditer(source)}
    return revision, files


def test_manifest_matches_macos_catalog_byte_for_byte():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    revision, files = swift_catalog()
    assert manifest["schema"] == 1
    assert manifest["id"] == "qwen3-asr-0.6b"
    assert manifest["provider"] == "qwen-local"
    assert manifest["revision"] == revision
    assert manifest["source"]["kind"] == "files"
    assert manifest["source"]["baseUrl"] == (
        f"https://huggingface.co/Qwen/Qwen3-ASR-0.6B/resolve/{revision}/"
    )
    listed = {f["path"]: (f["size"], f["sha256"]) for f in manifest["files"]}
    assert listed == files
    assert len(listed) == 9


def test_manifest_digest_is_canonical_and_matches_macos():
    """engine/model_cache.py canonical_digest is the one rule; macOS pins the same value."""
    from engine.model_cache import ModelManifest, canonical_digest

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = SWIFT.read_text(encoding="utf-8")
    expected = re.search(r'static let digest = "([0-9a-f]{64})"', source).group(1)
    assert manifest["digest"] == canonical_digest(manifest["files"]) == expected
    # The strict schema-1 parser must accept the file (digest is a required field, not an extra).
    ModelManifest.parse(MANIFEST.read_text(encoding="utf-8"))
