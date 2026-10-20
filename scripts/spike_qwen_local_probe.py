#!/usr/bin/env python3
"""Standalone Qwen3-ASR-0.6B CPU feasibility probe (PR-0, no engine imports).

Verifies or downloads the nine pinned files from the manifest, loads the model
through the qwen-asr package on CPU, transcribes one WAV twice (cold, warm) and
writes a JSON report. The report carries hashes, lengths and timings only;
transcript text is printed only with --print-transcript for local debugging.

The helpers above ``main`` import nothing heavier than the standard library so
the pytest suite can exercise them without torch or qwen-asr installed.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

CHUNK = 1 << 20
SIMILARITY_THRESHOLD = 0.8


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
        raise SystemExit(f"unsupported manifest: {path}")
    if not manifest["files"]:
        raise SystemExit(f"manifest lists no files: {path}")
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_files(manifest: dict, model_dir: Path) -> dict[str, str]:
    """Size is checked before the hash so a truncated 1.9 GB download fails fast."""
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
        # A private temp name per process: two probes sharing the directory must not
        # truncate or rename each other's partial download.
        tmp = target.with_name(f"{target.name}.{os.getpid()}-{secrets.token_hex(4)}.part")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(base + entry["path"], timeout=60) as response, tmp.open("wb") as out:
                for block in iter(lambda: response.read(CHUNK), b""):
                    out.write(block)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        print(f"downloaded {entry['path']} in {time.perf_counter() - started:.1f}s", file=sys.stderr)
    return verify_files(manifest, model_dir)


_STRIP = re.compile(r"[\s，。、！？,.!?;:；：\"'“”‘’()（）\[\]【】-]")


def normalize(text: str) -> str:
    return _STRIP.sub("", text).lower()


def matches(transcript: str, expected: str) -> bool:
    """Recognisable transcript: ordered similarity of the normalised strings."""
    a, b = normalize(transcript), normalize(expected)
    if not a or not b:
        return False
    if a == b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


def _peak_rss_sampler(stop: threading.Event, samples: list[float]) -> None:
    try:
        import psutil
    except Exception:
        return
    process = psutil.Process()
    while not stop.is_set():
        samples.append(process.memory_info().rss / 1_048_576)
        stop.wait(0.2)


def load_model(model_dir: Path) -> tuple[object, str, int]:
    """Load from the pinned directory only; the hub must never be contacted."""
    import torch
    from qwen_asr import Qwen3ASRModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.perf_counter()
    model = Qwen3ASRModel.from_pretrained(str(model_dir), dtype=torch.float32, device_map=device)
    return model, device, int((time.perf_counter() - started) * 1000)


def transcribe_twice(model: object, wav: Path) -> tuple[list[str], list[int]]:
    """The WAV is passed as a path, exactly as runtime/fastpath/stt.py QwenLocalASR does."""
    transcripts: list[str] = []
    latencies: list[int] = []
    for _ in range(2):
        started = time.perf_counter()
        results = model.transcribe([str(wav)])
        latencies.append(int((time.perf_counter() - started) * 1000))
        text = getattr(results[0], "text", "") if results else ""
        transcripts.append(text if isinstance(text, str) else "")
    return transcripts, latencies


def wav_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return round(handle.getnframes() / float(handle.getframerate() or 1), 2)


def dir_bytes(path: Path | None) -> int:
    if path is None or not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build_report(
    *,
    model_dir_status: dict[str, str],
    device: str,
    transcripts: list[str],
    latencies_ms: list[int],
    load_ms: int,
    expected: str,
    peak_rss_mb: float,
    frozen_dir_bytes: int,
    audio_seconds: float,
) -> dict:
    final = transcripts[-1] if transcripts else ""
    files_ok = all(v == "ok" for v in model_dir_status.values())
    matched = matches(final, expected)
    return {
        "schema": 1,
        "probe": "qwen3-asr-0.6b-cpu",
        "device": device,
        "files": model_dir_status,
        "files_ok": files_ok,
        "audio_seconds": audio_seconds,
        "latency_ms": {
            "load": load_ms,
            "cold": latencies_ms[0] if latencies_ms else None,
            "warm": latencies_ms[-1] if len(latencies_ms) > 1 else None,
        },
        "transcript_chars": len(final),
        "transcript_sha256": hashlib.sha256(final.encode("utf-8")).hexdigest(),
        "matches_expected": matched,
        "peak_rss_mb": round(peak_rss_mb, 1),
        "frozen_dir_bytes": frozen_dir_bytes,
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        # The spike passes only on CPU, with verified files and a recognisable transcript.
        "success": files_ok and device == "cpu" and matched,
    }


def _versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("qwen_asr", "transformers", "torch", "accelerate", "librosa", "numba"):
        try:
            from importlib.metadata import version

            out[name] = version(name.replace("_", "-"))
        except Exception:
            out[name] = "unknown"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--expected", default="测试一二三")
    parser.add_argument("--download", action="store_true", help="fetch missing files from the manifest source")
    parser.add_argument("--verify-only", action="store_true", help="stop after verifying (and downloading) files")
    parser.add_argument("--frozen-dir", type=Path, help="directory of the frozen build, for the size measurement")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--print-transcript", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    status = verify_files(manifest, args.model_dir)
    if args.download and any(v != "ok" for v in status.values()):
        status = download_missing(manifest, args.model_dir, status)
    if any(v != "ok" for v in status.values()):
        args.report.write_text(
            json.dumps({"schema": 1, "files": status, "files_ok": False, "success": False}, indent=2),
            encoding="utf-8",
        )
        print("model files not verified:", status, file=sys.stderr)
        return 2
    if args.verify_only:
        args.report.write_text(
            json.dumps({"schema": 1, "files": status, "files_ok": True}, indent=2), encoding="utf-8"
        )
        return 0
    if args.wav is None or not args.wav.is_file():
        print("--wav is required unless --verify-only", file=sys.stderr)
        return 2

    import threading

    stop = threading.Event()
    samples: list[float] = []
    sampler = threading.Thread(target=_peak_rss_sampler, args=(stop, samples), daemon=True)
    sampler.start()
    try:
        model, device, load_ms = load_model(args.model_dir)
        transcripts, latencies = transcribe_twice(model, args.wav)
    finally:
        stop.set()
        sampler.join(timeout=2)
    report = build_report(
        model_dir_status=status,
        device=device,
        transcripts=transcripts,
        latencies_ms=latencies,
        load_ms=load_ms,
        expected=args.expected,
        peak_rss_mb=max(samples) if samples else -1.0,
        frozen_dir_bytes=dir_bytes(args.frozen_dir),
        audio_seconds=wav_seconds(args.wav),
    )
    report["versions"] = _versions()
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.print_transcript:
        print(transcripts[-1])
    summary = (
        "device",
        "audio_seconds",
        "latency_ms",
        "transcript_chars",
        "matches_expected",
        "peak_rss_mb",
        "frozen_dir_bytes",
        "success",
    )
    print(json.dumps({k: report[k] for k in summary}))
    return 0 if report["success"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
