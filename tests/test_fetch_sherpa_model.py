"""Regression tests for the idempotent sherpa model fetcher (verified cache, T31)."""

from __future__ import annotations

import hashlib
import json

from engine.model_cache import canonical_digest
from scripts import fetch_sherpa_model

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


def files_manifest(model_id, files):
    entries = [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for p, b in files.items()]
    return {
        "schema": 1, "id": model_id, "provider": "sherpa", "architecture": "any", "revision": "r1",
        "license": "Apache-2.0", "source": {"kind": "files", "baseUrl": BASE},
        "files": entries, "digest": canonical_digest(entries),
    }


def test_existing_asr_bundle_still_downloads_missing_vad(tmp_path):
    asr = {"model.int8.onnx": b"weights", "tokens.txt": b"a\n"}
    vad = {"silero_vad.onnx": b"vad"}
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    for model_id, files in ((fetch_sherpa_model.SENSEVOICE_CACHE_ID, asr), (fetch_sherpa_model.SILERO_VAD_CACHE_ID, vad)):
        (manifests / f"{model_id}.json").write_text(json.dumps(files_manifest(model_id, files)))
    served = {**asr, **vad}
    downloads: list[str] = []

    def fetch(url, dest, max_bytes):
        downloads.append(url)
        dest.write_bytes(served[url.rsplit("/", 1)[-1]][: max_bytes + 1])

    models = tmp_path / "models"
    # First run: only the ASR bundle is pinned, so only it is installed.
    (manifests / f"{fetch_sherpa_model.SILERO_VAD_CACHE_ID}.json").rename(tmp_path / "vad.json")
    assert fetch_sherpa_model.main(["--dest", str(models)], manifests_dir=manifests, fetch=fetch) == 0
    assert sorted(downloads) == [BASE + "model.int8.onnx", BASE + "tokens.txt"]

    # Second run: the verified ASR revision is reused; only the VAD downloads.
    (tmp_path / "vad.json").rename(manifests / f"{fetch_sherpa_model.SILERO_VAD_CACHE_ID}.json")
    downloads.clear()
    assert fetch_sherpa_model.main(["--dest", str(models)], manifests_dir=manifests, fetch=fetch) == 0
    assert downloads == [BASE + "silero_vad.onnx"]
    vad_tree = models / fetch_sherpa_model.SILERO_VAD_CACHE_ID / "r1"
    assert (vad_tree / "silero_vad.onnx").read_bytes() == b"vad"
