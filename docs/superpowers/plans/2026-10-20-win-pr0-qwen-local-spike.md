# PR-0: Qwen3-ASR-0.6B frozen CPU feasibility spike

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, on a clean `windows-latest` runner with no Python or CUDA, that a PyInstaller-frozen executable can load the nine pinned Qwen3-ASR-0.6B files offline through the `qwen-asr` package on CPU, transcribe a known utterance, and report size, RAM and cold/warm latency, so PR-B and PR-C can be sized and the default route decided.

**Architecture:** A standalone probe script (no engine imports) reads a new pinned manifest `engine/model_manifests/qwen3-asr-0.6b.json`, verifies or downloads the files, runs the model twice, and writes a JSON report with hashes and timings but never transcript text. A workflow_dispatch-only workflow builds it with PyInstaller on `windows-latest`, synthesises the test utterance with Windows SAPI, runs the frozen exe, and uploads the report. The pytest in this PR pins the manifest to the exact bytes macOS already trusts.

**Tech Stack:** Python 3.12, `qwen-asr` (transformers 4.57.6 + CPU torch), PyInstaller 6, GitHub Actions `windows-latest`, PowerShell `System.Speech`.

---

### Task 1: Pin the Qwen3-ASR-0.6B manifest to the macOS catalog

**Files:**
- Create: `engine/model_manifests/qwen3-asr-0.6b.json`
- Test: `tests/test_qwen_local_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qwen_local_manifest.py
"""The Windows local Qwen manifest must pin exactly the bytes macOS trusts."""
import hashlib
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


def test_manifest_digest_matches_macos_expected_digest():
    """macOS computes one digest over the sorted file list; keep it identical."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = SWIFT.read_text(encoding="utf-8")
    expected = re.search(r'static let digest = "([0-9a-f]{64})"', source).group(1)
    assert manifest["digest"] == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_qwen_local_manifest.py -q`
Expected: FAIL with `FileNotFoundError` for the manifest.

- [ ] **Step 3: Find out how macOS computes `digest`**

Run: `grep -n -A20 "expectedDigest" macos/VoxLog/Services/ModelManifest.swift | head -40`
Expected: a function that hashes the file entries in a fixed order (path, size, sha256). Reproduce that exact rule in the test helper below only if the digest test fails after Step 4; if the Swift code hashes a canonical string, add a `manifest_digest()` helper to the test that builds the same string. Do not invent a new rule: the `digest` value in the JSON must equal the Swift constant `fa38dbb84faec2dc363a2761fbc628d1380ef0b5088e4d5836bc1be03984a0dc`.

- [ ] **Step 4: Write the manifest**

```json
{
  "schema": 1,
  "id": "qwen3-asr-0.6b",
  "provider": "qwen-local",
  "architecture": "any",
  "revision": "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
  "license": "Apache-2.0",
  "digest": "fa38dbb84faec2dc363a2761fbc628d1380ef0b5088e4d5836bc1be03984a0dc",
  "source": {
    "kind": "files",
    "baseUrl": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B/resolve/5eb144179a02acc5e5ba31e748d22b0cf3e303b0/"
  },
  "files": [
    {"path": "README.md", "size": 57456, "sha256": "5058416891bc47a2051557765997e8c42f8eb78a0e33c3e775bd17d4b0ba4d50"},
    {"path": "chat_template.json", "size": 1161, "sha256": "75a8cfca24f00de72d796fbfed6858fc9614ef3dabd8696684cc3bc03a9c58ff"},
    {"path": "config.json", "size": 6193, "sha256": "76d3ae4601ce939830b2517f4a6cadb86cc51316c3900af6b020b051c21a478c"},
    {"path": "generation_config.json", "size": 142, "sha256": "1da527824d81e07118facff437e03f2e24a23311e3bdeb2368973fe77e5f275c"},
    {"path": "merges.txt", "size": 1671853, "sha256": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"},
    {"path": "model.safetensors", "size": 1876091704, "sha256": "79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea"},
    {"path": "preprocessor_config.json", "size": 330, "sha256": "45e120a4eda2c20c5d7f2ea9354e63536bf35e27aa573fb7cdf78017b378770d"},
    {"path": "tokenizer_config.json", "size": 12487, "sha256": "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"},
    {"path": "vocab.json", "size": 2776833, "sha256": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"}
  ]
}
```

Check the license field against `README.md` in the Hugging Face repo at that revision; if it is not Apache-2.0, correct the string (this is documentation, not a hash input).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_qwen_local_manifest.py -q`
Expected: `2 passed`. If the digest test fails, implement the Swift rule from Step 3 in the test and keep the JSON value as the Swift constant.

- [ ] **Step 6: Run the existing manifest tests**

Run: `.venv/bin/python -m pytest tests -q -k "manifest" --tb=short`
Expected: all pass (`scripts/pin_model_manifest.py` and `fetch_sherpa_model.py` consumers accept schema 1 with an extra `digest` key; if a strict schema test rejects the extra key, add `digest` to its allowlist with a comment).

- [ ] **Step 7: Commit**

```bash
git add engine/model_manifests/qwen3-asr-0.6b.json tests/test_qwen_local_manifest.py
git commit -m "Pin Qwen3-ASR-0.6B manifest to the macOS catalog bytes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: Standalone probe script (download, verify, load, time, report)

**Files:**
- Create: `scripts/spike_qwen_local_probe.py`
- Test: `tests/test_spike_qwen_local_probe.py`

- [ ] **Step 1: Write the failing tests for the pure parts**

```python
# tests/test_spike_qwen_local_probe.py
import hashlib
import json
from pathlib import Path

import pytest

from scripts import spike_qwen_local_probe as probe


def _manifest(tmp_path: Path, files: dict[str, bytes]) -> Path:
    entries = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    ]
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 1, "id": "x", "source": {"kind": "files", "baseUrl": "https://example.invalid/"}, "files": entries}))
    return path


def test_verify_reports_missing_and_mismatched_files(tmp_path):
    manifest = _manifest(tmp_path, {"a.bin": b"aaa", "b.bin": b"bbb"})
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "a.bin").write_bytes(b"aaa")
    (model_dir / "b.bin").write_bytes(b"WRONG")
    status = probe.verify_files(probe.load_manifest(manifest), model_dir)
    assert status == {"a.bin": "ok", "b.bin": "sha256-mismatch"}


def test_verify_flags_size_before_hashing(tmp_path):
    manifest = _manifest(tmp_path, {"a.bin": b"aaa"})
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "a.bin").write_bytes(b"aaaa")
    assert probe.verify_files(probe.load_manifest(manifest), model_dir) == {"a.bin": "size-mismatch"}


def test_report_never_contains_transcript_text():
    report = probe.build_report(
        model_dir_status={"a": "ok"},
        device="cpu",
        transcripts=["秘密文本", "秘密文本"],
        latencies_ms=[1500, 400],
        expected="秘密文本",
        rss_mb=1234.5,
        exe_bytes=10,
    )
    dumped = json.dumps(report, ensure_ascii=False)
    assert "秘密文本" not in dumped
    assert report["transcript_sha256"] == hashlib.sha256("秘密文本".encode()).hexdigest()
    assert report["transcript_chars"] == 4
    assert report["matches_expected"] is True
    assert report["latency_ms"] == {"cold": 1500, "warm": 400}


def test_similarity_tolerates_punctuation_and_spaces():
    assert probe.matches("测试，一 二 三。", "测试一二三") is True
    assert probe.matches("完全不同", "测试一二三") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_spike_qwen_local_probe.py -q`
Expected: FAIL with `ModuleNotFoundError: scripts.spike_qwen_local_probe` (the `scripts` package already has an `__init__.py`; confirm with `ls scripts/__init__.py`, create an empty one if missing).

- [ ] **Step 3: Write the probe**

```python
#!/usr/bin/env python3
"""Standalone Qwen3-ASR-0.6B CPU feasibility probe (PR-0, no engine imports).

Verifies or downloads the nine pinned files from the manifest, loads the model
through the qwen-asr package on CPU, transcribes one WAV twice (cold, warm) and
writes a JSON report. The report carries hashes, lengths and timings only;
transcript text is printed only with --print-transcript for local debugging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

CHUNK = 1 << 20


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
        raise SystemExit(f"unsupported manifest: {path}")
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_files(manifest: dict, model_dir: Path) -> dict[str, str]:
    status: dict[str, str] = {}
    for entry in manifest["files"]:
        target = model_dir / entry["path"]
        if not target.is_file():
            status[entry["path"]] = "missing"
        elif target.stat().st_size != entry["size"]:
            status[entry["path"]] = "size-mismatch"
        elif sha256_file(target) != entry["sha256"]:
            status[entry["path"]] = "sha256-mismatch"
        else:
            status[entry["path"]] = "ok"
    return status


def download_missing(manifest: dict, model_dir: Path, status: dict[str, str]) -> dict[str, str]:
    base = manifest["source"]["baseUrl"]
    model_dir.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        if status[entry["path"]] == "ok":
            continue
        target = model_dir / entry["path"]
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(base + entry["path"], timeout=60) as response, tmp.open("wb") as out:
            for block in iter(lambda: response.read(CHUNK), b""):
                out.write(block)
        os.replace(tmp, target)
    return verify_files(manifest, model_dir)


_STRIP = re.compile(r"[\s，。、！？,.!?;:；：\"'“”‘’()（）\[\]【】-]")


def normalize(text: str) -> str:
    return _STRIP.sub("", text).lower()


def matches(transcript: str, expected: str) -> bool:
    a, b = normalize(transcript), normalize(expected)
    if not a or not b:
        return False
    if a == b or b in a:
        return True
    same = sum(1 for ch in b if ch in a)
    return same / len(b) >= 0.8


def transcribe_twice(model_dir: Path, wav: Path) -> tuple[list[str], list[int], str]:
    import torch
    from qwen_asr import Qwen3ASRModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = Qwen3ASRModel.from_pretrained(str(model_dir), dtype=torch.float32, device_map=device)
    transcripts: list[str] = []
    latencies: list[int] = []
    for _ in range(2):
        started = time.perf_counter()
        results = model.transcribe([str(wav)])
        latencies.append(int((time.perf_counter() - started) * 1000))
        text = getattr(results[0], "text", "") if results else ""
        transcripts.append(text if isinstance(text, str) else "")
    return transcripts, latencies, device


def rss_megabytes() -> float:
    try:
        import psutil  # optional

        return psutil.Process().memory_info().rss / 1_048_576
    except Exception:
        return -1.0


def build_report(*, model_dir_status: dict[str, str], device: str, transcripts: list[str],
                 latencies_ms: list[int], expected: str, rss_mb: float, exe_bytes: int) -> dict:
    final = transcripts[-1] if transcripts else ""
    return {
        "schema": 1,
        "probe": "qwen3-asr-0.6b-cpu",
        "device": device,
        "files": model_dir_status,
        "files_ok": all(v == "ok" for v in model_dir_status.values()),
        "latency_ms": {"cold": latencies_ms[0] if latencies_ms else None,
                       "warm": latencies_ms[-1] if len(latencies_ms) > 1 else None},
        "transcript_chars": len(final),
        "transcript_sha256": hashlib.sha256(final.encode("utf-8")).hexdigest(),
        "matches_expected": matches(final, expected),
        "rss_mb": round(rss_mb, 1),
        "executable_bytes": exe_bytes,
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--expected", default="测试一二三")
    parser.add_argument("--download", action="store_true", help="fetch missing files from the manifest source")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--print-transcript", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    status = verify_files(manifest, args.model_dir)
    if args.download and any(v != "ok" for v in status.values()):
        status = download_missing(manifest, args.model_dir, status)
    if any(v != "ok" for v in status.values()):
        args.report.write_text(json.dumps({"schema": 1, "files": status, "files_ok": False}, indent=2))
        print("model files not verified:", status, file=sys.stderr)
        return 2

    transcripts, latencies, device = transcribe_twice(args.model_dir, args.wav)
    exe = Path(sys.executable)
    report = build_report(
        model_dir_status=status, device=device, transcripts=transcripts, latencies_ms=latencies,
        expected=args.expected, rss_mb=rss_megabytes(),
        exe_bytes=exe.stat().st_size if getattr(sys, "frozen", False) and exe.exists() else 0,
    )
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.print_transcript:
        print(transcripts[-1])
    print(json.dumps({k: report[k] for k in ("device", "latency_ms", "transcript_chars", "matches_expected", "rss_mb")}))
    return 0 if report["transcript_chars"] > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spike_qwen_local_probe.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Ruff**

Run: `.venv/bin/ruff check scripts/spike_qwen_local_probe.py tests/test_spike_qwen_local_probe.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add scripts/spike_qwen_local_probe.py tests/test_spike_qwen_local_probe.py
git commit -m "Add standalone Qwen3-ASR-0.6B CPU probe for the Windows spike

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 3: PyInstaller spec for the probe

**Files:**
- Create: `scripts/qwen_local_probe.spec`

- [ ] **Step 1: Discover the runtime dependency set**

Run (in a scratch venv, not the repo venv):

```bash
python3 -m venv /tmp/qwen-spike && /tmp/qwen-spike/bin/pip install --quiet qwen-asr torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple && /tmp/qwen-spike/bin/pip show qwen-asr | sed -n '1,12p'
```

Expected: `Requires:` names `transformers` and the audio dependencies (`librosa`, `soundfile`, `torchaudio` or similar). Write the list down; each name becomes a `collect_all` below.

- [ ] **Step 2: Write the spec**

```python
# scripts/qwen_local_probe.spec
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PR-0 Qwen3-ASR-0.6B CPU probe (Windows)."""
from PyInstaller.utils.hooks import collect_all

PACKAGES = ["qwen_asr", "transformers", "torch", "tokenizers", "safetensors", "numpy", "soundfile", "librosa"]
# Replace/extend PACKAGES with the exact `Requires:` list from Task 3 Step 1.

datas, binaries, hiddenimports = [], [], []
for name in PACKAGES:
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["spike_qwen_local_probe.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="qwen-local-probe", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="qwen-local-probe")
```

A one-directory build (`COLLECT`) is used on purpose: it reports the real on-disk size and avoids one-file extraction time distorting the cold latency.

- [ ] **Step 3: Smoke the spec on macOS for syntax only**

Run: `/tmp/qwen-spike/bin/pip install --quiet pyinstaller && cd scripts && /tmp/qwen-spike/bin/pyinstaller --noconfirm --distpath /tmp/qwen-spike-dist qwen_local_probe.spec 2>&1 | tail -3`
Expected: `Building COLLECT ... completed successfully.` (a macOS build only proves the spec parses and the packages collect; Windows is proven in Task 4).

- [ ] **Step 4: Commit**

```bash
git add scripts/qwen_local_probe.spec
git commit -m "Add PyInstaller spec for the Qwen local probe

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 4: workflow_dispatch workflow on windows-latest

**Files:**
- Create: `.github/workflows/qwen-local-spike.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# Feasibility spike for Qwen3-ASR-0.6B frozen on Windows CPU (plan PR-0).
# Manual only. Produces a JSON report artifact; never a release artifact.
name: Qwen local spike

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  probe:
    runs-on: windows-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"

      - name: Install qwen-asr, CPU torch and PyInstaller
        run: |
          python -m pip install --upgrade pip
          python -m pip install qwen-asr torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
          python -m pip install pyinstaller psutil
          python -m pip show qwen-asr

      - name: Restore pinned model files
        id: model-cache
        uses: actions/cache@v4
        with:
          path: ${{ runner.temp }}\qwen3-asr-0.6b
          key: qwen3-asr-0.6b-fa38dbb84faec2dc363a2761fbc628d1380ef0b5088e4d5836bc1be03984a0dc

      - name: Download and verify model files (host Python, network allowed here only)
        run: >-
          python scripts/spike_qwen_local_probe.py
          --manifest engine/model_manifests/qwen3-asr-0.6b.json
          --model-dir "${{ runner.temp }}\qwen3-asr-0.6b"
          --wav NUL --download --report "${{ runner.temp }}\verify-only.json" || if ($LASTEXITCODE -eq 3) { exit 0 } else { exit $LASTEXITCODE }

      - name: Synthesize the test utterance with Windows SAPI
        shell: pwsh
        run: |
          Add-Type -AssemblyName System.Speech
          $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
          $voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
          if ($voice) { $s.SelectVoice($voice.VoiceInfo.Name) }
          $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
          $s.SetOutputToWaveFile("${{ runner.temp }}\utterance.wav", $fmt)
          $s.Speak("测试一二三")
          $s.Dispose()
          Get-Item "${{ runner.temp }}\utterance.wav" | Select-Object Length

      - name: Freeze the probe
        working-directory: scripts
        run: pyinstaller --noconfirm --distpath "${{ runner.temp }}\dist" qwen_local_probe.spec

      - name: Measure frozen size
        shell: pwsh
        run: |
          $bytes = (Get-ChildItem -Recurse "${{ runner.temp }}\dist\qwen-local-probe" | Measure-Object -Property Length -Sum).Sum
          "frozen_dir_bytes=$bytes" | Out-File -Append $env:GITHUB_OUTPUT
          "Frozen directory: $([math]::Round($bytes / 1MB)) MB"

      - name: Run the frozen probe offline (no PATH Python)
        shell: pwsh
        env:
          HF_HUB_OFFLINE: "1"
          TRANSFORMERS_OFFLINE: "1"
        run: |
          $env:PATH = "C:\Windows\System32;C:\Windows"
          & "${{ runner.temp }}\dist\qwen-local-probe\qwen-local-probe.exe" `
            --manifest "$PWD\engine\model_manifests\qwen3-asr-0.6b.json" `
            --model-dir "${{ runner.temp }}\qwen3-asr-0.6b" `
            --wav "${{ runner.temp }}\utterance.wav" `
            --report "${{ runner.temp }}\qwen-local-spike-report.json"
          Get-Content "${{ runner.temp }}\qwen-local-spike-report.json"

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: qwen-local-spike-report
          path: |
            ${{ runner.temp }}\qwen-local-spike-report.json
            ${{ runner.temp }}\verify-only.json
          if-no-files-found: warn
```

Notes for the executor: the download step passes `--wav NUL` so the host run stops after verification (exit code 3 means "no transcript", which is expected there and mapped to success). The frozen run clears `PATH` so a system Python cannot be picked up. If `actions/cache` refuses the 1.9 GB entry, keep the download step (it re-fetches) and note the time cost in the report.

- [ ] **Step 2: Validate the YAML locally**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/qwen-local-spike.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit, push, open the PR, trigger the workflow**

```bash
git add .github/workflows/qwen-local-spike.yml
git commit -m "Add manual Qwen local CPU spike workflow on windows-latest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin HEAD
gh pr create --title "PR-0: Qwen3-ASR-0.6B frozen CPU feasibility spike" --body-file - <<'EOF'
Feasibility spike for the Windows local ASR route (plan docs/superpowers/plans/2026-10-20-win-pr0-qwen-local-spike.md).
Adds the pinned manifest, a standalone probe, its PyInstaller spec and a manual windows-latest workflow. No runtime change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

`gh workflow run` only works once the workflow file exists on the default branch. Until this PR merges, trigger the spike by pushing a temporary commit that adds `push: branches: [<this branch>]` under `on:`; remove that commit (`git revert` or rebase) before the PR is marked ready. Record both commits in the PR description.

- [ ] **Step 4: Wait for the run and download the report**

Run: `gh run list --workflow qwen-local-spike.yml --limit 1` then `gh run download <run-id> -n qwen-local-spike-report -D /tmp/spike`
Expected: `/tmp/spike/qwen-local-spike-report.json` with `files_ok: true`, `transcript_chars > 0`, `latency_ms.cold`, `latency_ms.warm`, `rss_mb`, and the frozen size from the job log.

### Task 5: Decision report

**Files:**
- Create: `docs/verification/windows-qwen-local-spike-2026-10.md`

- [ ] **Step 1: Write the report from the artifact**

```markdown
# Windows Qwen3-ASR-0.6B frozen CPU spike (PR-0)

- Date: <YYYY-MM-DD>
- Runner: GitHub-hosted windows-latest (<image version from the job log>), CPU only
- Commit: <sha>; workflow run: <url>
- qwen-asr <version>, transformers <version>, torch <version> (CPU wheel), PyInstaller <version>

## Result

| Measure | Value |
|---|---|
| Nine pinned files verified offline | yes / no |
| Frozen directory size | <MB> |
| Cold transcribe latency (utterance "测试一二三", <seconds> s of audio) | <ms> |
| Warm transcribe latency | <ms> |
| Peak RSS | <MB> |
| Transcript matched expected | yes / no (sha256 <...>, <n> chars) |
| Clean shutdown | yes / no |

## Decision (spec §3.2 rule: warm ≤ 6 s for a 10-second utterance)

The test utterance is about 2 s long; scale the warm latency linearly to 10 s
for the rule. Measured warm × 5 = <ms> → **Qwen local default** | **sherpa-sensevoice default, Qwen local opt-in**.

## What PR-B and PR-C take from this

- PyInstaller `collect_all` list that worked: <list>
- Frozen size budget for `voxagent-backend.exe`: <MB>
- Offline loading confirmed with `Qwen3ASRModel.from_pretrained(<dir>)` and `HF_HUB_OFFLINE=1`: yes / no
- Problems seen (DLL load, dtype, processor assets): <list or none>
```

Fill every placeholder from the artifact and job log; a report with an unfilled field is not done.

- [ ] **Step 2: Commit and push**

```bash
git add docs/verification/windows-qwen-local-spike-2026-10.md
git commit -m "Record Qwen local CPU spike result and default-route decision

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
```

### Task 6: Codex audit and hand-off

- [ ] **Step 1: Run the standard Codex audit** from the master plan (`docs/superpowers/plans/2026-10-20-windows-voice-to-context.md`, "Standard Codex audit procedure") and append the record to `docs/verification/windows-voice-flow-2026-10.md`.

- [ ] **Step 2: Report** to the owner: the decision line from the report, the frozen size, and whether the default route rule chose Qwen local. PR-B and PR-C sub-plans are written next with these numbers.
