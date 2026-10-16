"""T31: the operator scripts only install verified, pinned model manifests.

No test touches the network: archives are built in memory and the fetcher is
injected.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

from engine.model_cache import ModelManifest, active_revision
from scripts import fetch_sherpa_model, pin_model_manifest

PREFIX = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
MEMBERS = {"model.int8.onnx": b"weights" * 50, "tokens.txt": b"a\nb\n"}


def build_archive(path: Path, members=MEMBERS, extra=None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        dir_info = tarfile.TarInfo(PREFIX)
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        for name, data in {**members, **(extra or {})}.items():
            info = tarfile.TarInfo(f"{PREFIX}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def pin(tmp_path: Path, archive: Path, url: str) -> Path:
    out = tmp_path / "manifests" / "sensevoice-int8.json"
    out.parent.mkdir(exist_ok=True)
    code = pin_model_manifest.main([
        "--archive", str(archive), "--url", url, "--id", "sensevoice-int8",
        "--provider", "sherpa-sensevoice", "--revision", "2024-07-17", "--license", "Apache-2.0",
        "--strip-prefix", PREFIX, "--out", str(out),
    ])
    assert code == 0
    return out


URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{PREFIX}.tar.bz2"


def test_pinning_writes_a_valid_manifest_from_a_local_archive(tmp_path):
    archive = tmp_path / "bundle.tar.bz2"
    data = build_archive(archive, extra={"README.md": b"docs"})
    manifest = ModelManifest.parse(pin(tmp_path, archive, URL).read_text())
    # Every regular member is pinned: the cache refuses unlisted members.
    assert [f.path for f in manifest.files] == ["README.md", "model.int8.onnx", "tokens.txt"]
    assert manifest.source == {
        "kind": "archive", "url": URL, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "stripPrefix": PREFIX + "/",
    }


def test_pinning_refuses_links_and_members_outside_the_prefix(tmp_path):
    for name, kind in ((f"{PREFIX}/link", tarfile.SYMTYPE), ("elsewhere/file.bin", tarfile.REGTYPE)):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
            tar.addfile(info, io.BytesIO(b""))
        archive = tmp_path / "bad.tar.bz2"
        archive.write_bytes(buffer.getvalue())
        out = tmp_path / "out.json"
        code = pin_model_manifest.main([
            "--archive", str(archive), "--url", URL, "--id", "sensevoice-int8", "--provider", "p",
            "--revision", "r", "--license", "l", "--strip-prefix", PREFIX, "--out", str(out),
        ])
        assert code != 0 and not out.exists()


def test_fetch_refuses_when_no_manifest_is_pinned(tmp_path, capsys):
    calls = []
    code = fetch_sherpa_model.main(
        ["--dest", str(tmp_path / "models")], manifests_dir=tmp_path / "none", fetch=lambda *a: calls.append(a)
    )
    assert code != 0 and calls == []
    assert "no pinned manifest" in capsys.readouterr().err


def test_fetch_installs_the_pinned_manifest_and_verifies_it(tmp_path):
    archive = tmp_path / "bundle.tar.bz2"
    data = build_archive(archive)
    manifests = pin(tmp_path, archive, URL).parent

    def fetch(url, dest, max_bytes):
        assert url == URL
        Path(dest).write_bytes(data[: max_bytes + 1])

    models = tmp_path / "models"
    assert fetch_sherpa_model.main(["--dest", str(models)], manifests_dir=manifests, fetch=fetch) == 0
    assert active_revision(models, "sensevoice-int8") == "2024-07-17"
    assert (models / "sensevoice-int8" / "2024-07-17" / "tokens.txt").read_bytes() == MEMBERS["tokens.txt"]


def test_fetch_rejects_a_tampered_download(tmp_path):
    archive = tmp_path / "bundle.tar.bz2"
    build_archive(archive)
    manifests = pin(tmp_path, archive, URL).parent
    tampered = tmp_path / "tampered.tar.bz2"
    bad = build_archive(tampered, members={**MEMBERS, "tokens.txt": b"x\ny\n"})

    def fetch(url, dest, max_bytes):
        Path(dest).write_bytes(bad[: max_bytes + 1])

    models = tmp_path / "models"
    assert fetch_sherpa_model.main(["--dest", str(models)], manifests_dir=manifests, fetch=fetch) != 0
    assert active_revision(models, "sensevoice-int8") is None


def test_the_fetch_script_no_longer_downloads_unverified():
    source = Path(fetch_sherpa_model.__file__).read_text(encoding="utf-8")
    assert "urlretrieve" not in source and "extractall" not in source


def test_pinning_checks_limits_before_reading(tmp_path, monkeypatch):
    archive = tmp_path / "bundle.tar.bz2"
    build_archive(archive, extra={f"f{i}.bin": b"x" for i in range(5)})
    monkeypatch.setattr(pin_model_manifest, "MAX_FILES", 3)
    out = tmp_path / "out.json"
    code = pin_model_manifest.main([
        "--archive", str(archive), "--url", URL, "--id", "sensevoice-int8", "--provider", "p",
        "--revision", "r", "--license", "l", "--strip-prefix", PREFIX, "--out", str(out),
    ])
    assert code != 0 and not out.exists()


HF_BASE = "https://huggingface.co/owner/repo/resolve/0123456789abcdef0123456789abcdef01234567/"


def test_pinning_single_files_writes_a_files_manifest(tmp_path):
    folder = tmp_path / "downloaded"
    folder.mkdir()
    (folder / "model.int8.onnx").write_bytes(b"weights" * 10)
    (folder / "tokens.txt").write_bytes(b"<unk> 0\n")
    out = tmp_path / "sensevoice-int8.json"
    code = pin_model_manifest.main([
        "--files-dir", str(folder), "--file", "tokens.txt", "--file", "model.int8.onnx",
        "--base-url", HF_BASE, "--id", "sensevoice-int8", "--provider", "sherpa-sensevoice",
        "--revision", "2024-07-17", "--license", "FunASR-Model-License", "--out", str(out),
    ])
    assert code == 0
    manifest = ModelManifest.parse(out.read_text())
    assert manifest.source == {"kind": "files", "baseUrl": HF_BASE}
    assert [(f.path, f.size) for f in manifest.files] == [("model.int8.onnx", 70), ("tokens.txt", 8)]
    assert manifest.files[1].sha256 == hashlib.sha256(b"<unk> 0\n").hexdigest()


def test_pinning_files_refuses_links_missing_files_and_both_modes(tmp_path):
    folder = tmp_path / "downloaded"
    folder.mkdir()
    (folder / "real.bin").write_bytes(b"x")
    (folder / "link.bin").symlink_to(folder / "real.bin")
    base = ["--base-url", HF_BASE, "--id", "silero-vad", "--provider", "p", "--revision", "r",
            "--license", "MIT", "--out", str(tmp_path / "out.json")]
    for extra in (["--file", "link.bin"], ["--file", "missing.bin"], ["--file", "../real.bin"]):
        assert pin_model_manifest.main(["--files-dir", str(folder), *extra, *base]) != 0
    assert not (tmp_path / "out.json").exists()


def test_the_shipped_manifests_pin_the_sherpa_models():
    manifests = Path(fetch_sherpa_model.MANIFESTS_DIR)
    for model_id in (fetch_sherpa_model.SENSEVOICE_CACHE_ID, fetch_sherpa_model.SILERO_VAD_CACHE_ID):
        manifest = ModelManifest.parse((manifests / f"{model_id}.json").read_text(encoding="utf-8"))
        assert manifest.id == model_id
        assert manifest.source["kind"] == "files"


def test_files_mode_checks_limits_before_hashing(tmp_path, monkeypatch):
    folder = tmp_path / "downloaded"
    folder.mkdir()
    (folder / "big.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(pin_model_manifest, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(pin_model_manifest, "_sha256", lambda path: (_ for _ in ()).throw(AssertionError("hashed")))
    code = pin_model_manifest.main([
        "--files-dir", str(folder), "--file", "big.bin", "--base-url", HF_BASE, "--id", "silero-vad",
        "--provider", "p", "--revision", "r", "--license", "MIT", "--out", str(tmp_path / "out.json"),
    ])
    assert code != 0 and not (tmp_path / "out.json").exists()


def test_offline_import_and_legacy_reuse_avoid_downloads(tmp_path):
    files = {"model.int8.onnx": b"weights" * 30, "tokens.txt": b"<unk> 0\n"}
    folder = tmp_path / "copied"
    folder.mkdir()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    pin_model_manifest.main([
        "--files-dir", str(folder), "--file", "model.int8.onnx", "--file", "tokens.txt", "--base-url", HF_BASE,
        "--id", "sensevoice-int8", "--provider", "p", "--revision", "r1", "--license", "l",
        "--out", str(manifests / "sensevoice-int8.json"),
    ])

    def no_network(url, dest, max_bytes):
        raise AssertionError(f"downloaded {url}")

    # Offline: from the copied folder, verified, no network.
    models = tmp_path / "models"
    assert fetch_sherpa_model.main(["--dest", str(models), "--from", str(folder)], manifests_dir=manifests,
                                   fetch=no_network, legacy_dirs=[]) == 0
    assert active_revision(models, "sensevoice-int8") == "r1"

    # Upgrade: an old unverified bundle with identical bytes is adopted.
    legacy = tmp_path / "old-bundle"
    legacy.mkdir()
    for name, data in files.items():
        (legacy / name).write_bytes(data)
    fresh = tmp_path / "fresh"
    assert fetch_sherpa_model.main(["--dest", str(fresh)], manifests_dir=manifests, fetch=no_network,
                                   legacy_dirs=[legacy]) == 0
    assert active_revision(fresh, "sensevoice-int8") == "r1"
    assert sorted(p.name for p in legacy.iterdir()) == ["model.int8.onnx", "tokens.txt"], "legacy left as it was"

    # A corrupted copy is refused, nothing activated.
    (folder / "tokens.txt").write_bytes(b"truncated")
    broken = tmp_path / "broken"
    assert fetch_sherpa_model.main(["--dest", str(broken), "--from", str(folder)], manifests_dir=manifests,
                                   fetch=no_network, legacy_dirs=[]) != 0
    assert active_revision(broken, "sensevoice-int8") is None
