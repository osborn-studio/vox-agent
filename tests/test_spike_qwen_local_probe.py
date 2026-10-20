"""Pure parts of the PR-0 Qwen local probe: verification, similarity and the report shape."""

import hashlib
import json
from pathlib import Path

from scripts import spike_qwen_local_probe as probe


def _manifest(tmp_path: Path, files: dict[str, bytes]) -> Path:
    entries = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    ]
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "x",
                "source": {"kind": "files", "baseUrl": "https://example.invalid/"},
                "files": entries,
            }
        )
    )
    return path


def test_verify_reports_missing_and_mismatched_files(tmp_path):
    manifest = _manifest(tmp_path, {"a.bin": b"aaa", "b.bin": b"bbb", "c.bin": b"ccc"})
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "a.bin").write_bytes(b"aaa")
    (model_dir / "b.bin").write_bytes(b"ccc")  # same length: must be a hash, not a size, mismatch
    status = probe.verify_files(probe.load_manifest(manifest), model_dir)
    assert status == {"a.bin": "ok", "b.bin": "sha256-mismatch", "c.bin": "missing"}


def test_verify_flags_size_before_hashing(tmp_path):
    manifest = _manifest(tmp_path, {"a.bin": b"aaa"})
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "a.bin").write_bytes(b"aaaa")
    assert probe.verify_files(probe.load_manifest(manifest), model_dir) == {"a.bin": "size-mismatch"}


def test_load_manifest_rejects_other_schemas(tmp_path):
    import pytest

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 2, "files": []}))
    with pytest.raises(SystemExit):
        probe.load_manifest(path)


def test_report_never_contains_transcript_text():
    report = probe.build_report(
        model_dir_status={"a": "ok"},
        device="cpu",
        transcripts=["秘密文本", "秘密文本"],
        latencies_ms=[1500, 400],
        load_ms=12000,
        expected="秘密文本",
        peak_rss_mb=1234.5,
        frozen_dir_bytes=10,
        audio_seconds=10.2,
    )
    dumped = json.dumps(report, ensure_ascii=False)
    assert "秘密文本" not in dumped
    assert report["transcript_sha256"] == hashlib.sha256("秘密文本".encode()).hexdigest()
    assert report["transcript_chars"] == 4
    assert report["matches_expected"] is True
    assert report["latency_ms"] == {"load": 12000, "cold": 1500, "warm": 400}
    assert report["audio_seconds"] == 10.2
    assert report["peak_rss_mb"] == 1234.5
    assert report["frozen_dir_bytes"] == 10
    assert report["success"] is True


def test_success_requires_cpu_verified_files_and_a_match():
    kwargs = dict(
        transcripts=["x"],
        latencies_ms=[1, 1],
        load_ms=1,
        expected="x",
        peak_rss_mb=1.0,
        frozen_dir_bytes=1,
        audio_seconds=1.0,
    )
    assert probe.build_report(model_dir_status={"a": "ok"}, device="cpu", **kwargs)["success"] is True
    assert probe.build_report(model_dir_status={"a": "missing"}, device="cpu", **kwargs)["success"] is False
    assert probe.build_report(model_dir_status={"a": "ok"}, device="cuda", **kwargs)["success"] is False
    mismatch = probe.build_report(model_dir_status={"a": "ok"}, device="cpu", **{**kwargs, "expected": "zz"})
    assert mismatch["success"] is False
    assert mismatch["matches_expected"] is False


def test_report_handles_a_single_or_empty_transcribe_run():
    one = probe.build_report(
        model_dir_status={"a": "ok"}, device="cpu", transcripts=["x"], latencies_ms=[7],
        load_ms=1, expected="x", peak_rss_mb=1.0, frozen_dir_bytes=1, audio_seconds=1.0,
    )
    assert one["latency_ms"] == {"load": 1, "cold": 7, "warm": None}
    none = probe.build_report(
        model_dir_status={"a": "ok"}, device="cpu", transcripts=[], latencies_ms=[],
        load_ms=1, expected="x", peak_rss_mb=1.0, frozen_dir_bytes=1, audio_seconds=1.0,
    )
    assert none["latency_ms"] == {"load": 1, "cold": None, "warm": None}
    assert none["transcript_chars"] == 0
    assert none["success"] is False


def test_similarity_tolerates_punctuation_and_spaces():
    assert probe.matches("测试，一 二 三。", "测试一二三") is True
    assert probe.matches("完全不同", "测试一二三") is False
    assert probe.matches("", "测试一二三") is False
    assert probe.matches("Testing, one two three!", "testing one two three") is True
    # One dropped character out of twenty is still a recognisable transcript ...
    assert probe.matches("今天下午三点我们开会讨论新的对话框设计", "今天下午三点我们开会讨论新的对话框设计请") is True
    # ... a different sentence that shares most of its characters is not.
    assert probe.matches("设计框话对的新论讨会开们我点三午下天今", "今天下午三点我们开会讨论新的对话框设计") is False


def test_wav_seconds_reads_pcm_header(tmp_path):
    import wave

    path = tmp_path / "u.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000 * 3)
    assert probe.wav_seconds(path) == 3.0


def test_dir_bytes_sums_files_only(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "sub" / "b").write_bytes(b"123")
    assert probe.dir_bytes(tmp_path) == 8
    assert probe.dir_bytes(None) == 0
    assert probe.dir_bytes(tmp_path / "nope") == 0


def test_load_manifest_rejects_an_empty_file_list(tmp_path):
    """Codex adversarial finding: an empty list verified as ok and --verify-only exited 0."""
    import pytest

    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 1, "files": []}))
    with pytest.raises(SystemExit):
        probe.load_manifest(path)


def test_download_uses_a_private_temp_file_and_leaves_none_behind(tmp_path, monkeypatch):
    """Codex adversarial finding: two probes sharing <name>.part could clobber each other."""
    import io
    import urllib.request

    manifest = _manifest(tmp_path, {"a.bin": b"aaa", "b.bin": b"bbb"})
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "a.bin").write_bytes(b"aaa")
    (model_dir / "b.bin.part").write_bytes(b"stale")  # another process's temp file must survive
    seen_urls: list[str] = []

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout):
        seen_urls.append(url)
        return _Response(b"bbb")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    loaded = probe.load_manifest(manifest)
    status = probe.verify_files(loaded, model_dir)
    assert status == {"a.bin": "ok", "b.bin": "missing"}
    assert probe.download_missing(loaded, model_dir, status) == {"a.bin": "ok", "b.bin": "ok"}
    assert seen_urls == ["https://example.invalid/b.bin"]
    assert (model_dir / "b.bin").read_bytes() == b"bbb"
    assert (model_dir / "b.bin.part").read_bytes() == b"stale"
    assert sorted(p.name for p in model_dir.iterdir()) == ["a.bin", "b.bin", "b.bin.part"]
