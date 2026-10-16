"""T31: model cache manifests, verified download and atomic activation.

All downloads go through an injected fetcher; no test touches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from engine.model_cache import (
    ModelCacheError,
    ModelManifest,
    active_revision,
    canonical_digest,
    install,
    verify_active,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILES = {"model.int8.onnx": b"weights" * 100, "tokens.txt": b"a\nb\n", "sub/config.json": b"{}"}


def entries(files=FILES):
    return [{"path": p, "size": len(b), "sha256": sha(b)} for p, b in files.items()]


def manifest_dict(files=FILES, **extra):
    data = {
        "schema": 1,
        "id": "sensevoice-int8",
        "provider": "sherpa-sensevoice",
        "architecture": "any",
        "revision": "2024-07-17",
        "license": "Apache-2.0",
        "source": {"kind": "files", "baseUrl": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"},
        "files": entries(files),
        "digest": canonical_digest(entries(files)),
    }
    data.update(extra)
    return data


class Fetcher:
    """Serves bytes by URL suffix and records requests."""

    def __init__(self, files=FILES, tamper=None):
        self.files = dict(files)
        self.tamper = tamper or {}
        self.requests: list[str] = []

    def __call__(self, url: str, dest: Path, max_bytes: int) -> None:
        self.requests.append(url)
        name = url.rsplit("/", 1)[-1]
        key = next(k for k in list(self.files) + list(self.tamper) if k.rsplit("/", 1)[-1] == name)
        data = self.tamper.get(key, self.files.get(key))
        with open(dest, "wb") as out:
            out.write(data[: max_bytes + 1])


def test_the_digest_matches_the_macos_model_manifest_algorithm():
    # Same canonical form as macos/VoxLog/Services/ModelManifest.swift.
    files = [{"path": "b.bin", "size": 3, "sha256": "a" * 64}, {"path": "a.bin", "size": 1, "sha256": "b" * 64}]
    canonical = (
        '[{"path":"a.bin","sha256":"' + "b" * 64 + '","size":1},'
        '{"path":"b.bin","sha256":"' + "a" * 64 + '","size":3}]'
    )
    assert canonical_digest(files) == sha(canonical.encode())


@pytest.mark.parametrize(
    "path",
    ["", "../x", "a/../b", "/abs", "C:/x", "a\\b", "a//b", "a/./b", "trailing.", "sp ace", "é.bin"],
)
def test_unsafe_paths_are_refused(path):
    files = {path: b"x", "ok.bin": b"y"}
    data = manifest_dict()
    data["files"] = entries(files)
    data["digest"] = canonical_digest(data["files"])
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(data))


def test_case_collisions_digest_mismatch_and_unknown_fields_are_refused():
    for files in ({"A.bin": b"1", "a.bin": b"2"}, {"dir": b"1", "dir/x": b"2"}):
        data = manifest_dict(files)
        with pytest.raises(ModelCacheError):
            ModelManifest.parse(json.dumps(data))
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(manifest_dict(digest="0" * 64)))
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(manifest_dict(extra="x")))
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(manifest_dict(source={"kind": "files", "baseUrl": "http://x/"})))


def test_install_downloads_into_staging_verifies_and_activates_atomically(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    fetch = Fetcher()
    result = install(manifest, tmp_path / "models", fetch)
    assert result.downloaded == 3 and result.reused is False
    assert active_revision(tmp_path / "models", "sensevoice-int8") == "2024-07-17"
    root = tmp_path / "models" / "sensevoice-int8" / "2024-07-17"
    assert (root / "sub" / "config.json").read_bytes() == b"{}"
    assert not list((tmp_path / "models" / "sensevoice-int8").glob(".staging-*")), "staging cleaned"
    assert verify_active(tmp_path / "models", manifest).ok


def test_a_verified_install_is_reused_without_downloading(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    install(manifest, tmp_path / "models", Fetcher())
    again = Fetcher()
    result = install(manifest, tmp_path / "models", again)
    assert result.reused is True and again.requests == []


def test_a_corrupted_file_is_detected_and_only_it_is_downloaded_again(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    install(manifest, tmp_path / "models", Fetcher())
    target = tmp_path / "models" / "sensevoice-int8" / "2024-07-17" / "tokens.txt"
    target.write_bytes(b"tampered")
    status = verify_active(tmp_path / "models", manifest)
    assert not status.ok and status.problems == ["tokens.txt: digest mismatch"]
    fetch = Fetcher()
    result = install(manifest, tmp_path / "models", fetch)
    assert [u.rsplit("/", 1)[-1] for u in fetch.requests] == ["tokens.txt"]
    assert result.downloaded == 1
    assert verify_active(tmp_path / "models", manifest).ok


def test_a_tampered_download_never_becomes_active(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    with pytest.raises(ModelCacheError):
        install(manifest, tmp_path / "models", Fetcher(tamper={"tokens.txt": b"evil\n"}))
    assert active_revision(tmp_path / "models", "sensevoice-int8") is None
    with pytest.raises(ModelCacheError):
        install(manifest, tmp_path / "models", Fetcher(tamper={"tokens.txt": b"a\nb\n" + b"x" * 10_000}))
    assert not (tmp_path / "models" / "sensevoice-int8" / "2024-07-17").exists()


def test_extra_files_and_symlinks_in_the_active_tree_fail_verification(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    install(manifest, tmp_path / "models", Fetcher())
    root = tmp_path / "models" / "sensevoice-int8" / "2024-07-17"
    (root / "extra.bin").write_bytes(b"x")
    assert "extra.bin: not in manifest" in verify_active(tmp_path / "models", manifest).problems
    (root / "extra.bin").unlink()
    if os.name != "nt":
        (root / "tokens.txt").unlink()
        os.symlink("/etc/hosts", root / "tokens.txt")
        assert "tokens.txt: not a regular file" in verify_active(tmp_path / "models", manifest).problems


def test_a_symlinked_cache_root_is_refused(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink creation needs privileges on Windows")
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "models")
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    with pytest.raises(ModelCacheError):
        install(manifest, tmp_path / "models", Fetcher())


def _archive(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, data
                tar.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
    return buffer.getvalue()


def archive_manifest(blob, files=FILES, prefix="bundle/"):
    data = manifest_dict(files)
    data["source"] = {
        "kind": "archive",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/bundle.tar.bz2",
        "size": len(blob),
        "sha256": sha(blob),
        "stripPrefix": prefix,
    }
    return ModelManifest.parse(json.dumps(data))


def good_members(prefix="bundle/"):
    return [(prefix, None, "dir")] + [(prefix + p, b, "file") for p, b in FILES.items()]


def test_an_archive_is_extracted_safely_and_must_match_the_manifest(tmp_path):
    blob = _archive(good_members())
    manifest = archive_manifest(blob)
    result = install(manifest, tmp_path / "models", Fetcher(files={"bundle.tar.bz2": blob}))
    assert result.downloaded == 1
    assert verify_active(tmp_path / "models", manifest).ok


@pytest.mark.parametrize(
    "bad",
    [
        ("bundle/../escape.txt", b"x", "file"),
        ("/abs.txt", b"x", "file"),
        ("bundle/link", "/etc/passwd", "symlink"),
        ("bundle/unexpected.bin", b"x", "file"),
    ],
)
def test_archive_escapes_links_and_unexpected_members_are_refused(tmp_path, bad):
    blob = _archive(good_members() + [bad])
    manifest = archive_manifest(blob)
    with pytest.raises(ModelCacheError):
        install(manifest, tmp_path / "models", Fetcher(files={"bundle.tar.bz2": blob}))
    assert active_revision(tmp_path / "models", "sensevoice-int8") is None
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "models" / "escape.txt").exists()


def test_an_archive_member_larger_than_its_manifest_entry_is_refused(tmp_path):
    members = good_members()
    members[1] = (members[1][0], members[1][1] + b"0" * 5_000_000, "file")  # highly compressible
    blob = _archive(members)
    with pytest.raises(ModelCacheError):
        install(archive_manifest(blob), tmp_path / "models", Fetcher(files={"bundle.tar.bz2": blob}))


def test_the_shipped_manifests_are_valid():
    shipped = sorted((Path(__file__).resolve().parents[1] / "engine" / "model_manifests").glob("*.json"))
    for path in shipped:
        ModelManifest.parse(path.read_text(encoding="utf-8"))


def test_leftovers_of_an_interrupted_install_are_removed(tmp_path):
    model_dir = tmp_path / "models" / "sensevoice-int8"
    (model_dir / ".staging-deadbeef" / "sub").mkdir(parents=True)
    (model_dir / ".staging-deadbeef" / "sub" / "partial.bin").write_bytes(b"half")
    (model_dir / ".retired-cafe").mkdir()
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    install(manifest, tmp_path / "models", Fetcher())
    assert sorted(p.name for p in model_dir.iterdir()) == ["2024-07-17", "ACTIVE"]


def test_downloads_follow_redirects_only_to_allowed_hosts():
    from engine.model_cache import redirect_allowed

    assert redirect_allowed("https://objects.githubusercontent.com/github-production-release-asset/x")
    assert redirect_allowed("https://release-assets.githubusercontent.com/x")
    for url in ("http://objects.githubusercontent.com/x", "https://evil.example/x",
                "https://objects.githubusercontent.com.evil.example/x", "https://user@github.com/x"):
        assert not redirect_allowed(url), url


def test_the_http_fetcher_caps_the_bytes_written(tmp_path):
    from engine.model_cache import http_fetch

    class Response(io.BytesIO):
        def geturl(self):
            return "https://objects.githubusercontent.com/x"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    dest = tmp_path / "file"
    http_fetch("https://github.com/x", dest, 10, opener=lambda url, timeout: Response(b"0123456789ABCDEF"))
    assert dest.read_bytes() == b"0123456789A", "max_bytes + 1, so oversize is detectable"
    with pytest.raises(ModelCacheError):
        http_fetch("https://evil.example/x", dest, 10, opener=lambda url, timeout: Response(b""))


def test_the_asr_path_prefers_the_verified_cache(tmp_path, monkeypatch):
    from runtime import local_asr_paths

    monkeypatch.delenv("VOXAGENT_SHERPA_MODEL_DIR", raising=False)
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: tmp_path)
    monkeypatch.setattr(local_asr_paths, "legacy_voxlog_models_root", lambda: tmp_path / "legacy")
    (tmp_path / local_asr_paths.SENSEVOICE_CACHE_ID / "2024-07-17").mkdir(parents=True)
    (tmp_path / local_asr_paths.SENSEVOICE_CACHE_ID / "ACTIVE").write_text("2024-07-17\n")
    assert local_asr_paths.resolve_sherpa_model_dir() == tmp_path / local_asr_paths.SENSEVOICE_CACHE_ID / "2024-07-17"
    (tmp_path / local_asr_paths.SENSEVOICE_CACHE_ID / "ACTIVE").unlink()
    assert local_asr_paths.resolve_sherpa_model_dir() == tmp_path / local_asr_paths.SHERPA_BUNDLE


@pytest.mark.parametrize("field", ["id", "revision"])
@pytest.mark.parametrize("value", ["..", ".", ".staging-x", ".retired-x", "ACTIVE", "active", "CON", "nul.txt", "a."])
def test_ids_and_revisions_are_plain_directory_names(field, value):
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(manifest_dict(**{field: value})))


@pytest.mark.parametrize("path", ["CON", "sub/nul", "aux.txt", "com1.bin", "LPT9", "sub/Con.json"])
def test_windows_device_names_are_refused_as_file_paths(path):
    with pytest.raises(ModelCacheError):
        ModelManifest.parse(json.dumps(manifest_dict(files={path: b"x"})))


@pytest.mark.parametrize("pointer", ["D:outside", "C:\\\\x", "a:stream", "../x", "CON", ".staging-1", "x" * 200])
def test_the_asr_resolver_ignores_unsafe_pointers(tmp_path, monkeypatch, pointer):
    from runtime import local_asr_paths

    models = tmp_path / "models"
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: models)
    model_dir = models / local_asr_paths.SENSEVOICE_CACHE_ID
    model_dir.mkdir(parents=True)
    (model_dir / "ACTIVE").write_text(pointer)
    assert local_asr_paths._verified_cache_dir(local_asr_paths.SENSEVOICE_CACHE_ID) is None


def test_the_asr_resolver_and_verify_refuse_symlinked_model_dirs(tmp_path, monkeypatch):
    from runtime import local_asr_paths

    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    real = tmp_path / "elsewhere"
    install(manifest, real, Fetcher())
    models = tmp_path / "models"
    models.mkdir()
    (models / "sensevoice-int8").symlink_to(real / "sensevoice-int8", target_is_directory=True)
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: models)
    assert local_asr_paths._verified_cache_dir("sensevoice-int8") is None
    assert not verify_active(models, manifest).ok
    assert local_asr_paths._verified_cache_dir.__module__ == "runtime.local_asr_paths"
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: real)
    assert local_asr_paths._verified_cache_dir("sensevoice-int8") == real / "sensevoice-int8" / "2024-07-17"


def test_replacing_a_damaged_revision_never_points_at_a_missing_tree(tmp_path, monkeypatch):
    import engine.model_cache as cache

    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    root = tmp_path / "models"
    install(manifest, root, Fetcher())
    (root / "sensevoice-int8" / "2024-07-17" / "tokens.txt").write_bytes(b"damaged")
    real_replace = os.replace
    calls = []

    def crash_on_activation(src, dst):
        calls.append(Path(dst).name)
        if Path(src).name.startswith(".staging-"):
            raise OSError("power loss")
        return real_replace(src, dst)

    monkeypatch.setattr(cache.os, "replace", crash_on_activation)
    with pytest.raises(OSError):
        install(manifest, root, Fetcher())
    monkeypatch.setattr(cache.os, "replace", real_replace)
    # The damaged revision was deactivated first: nothing points at a gap.
    assert active_revision(root, "sensevoice-int8") is None
    result = install(manifest, root, Fetcher())
    assert result.revision == "2024-07-17" and verify_active(root, manifest).ok


def test_a_second_concurrent_install_is_refused(tmp_path):
    import engine.model_cache as cache

    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    root = tmp_path / "models"
    root.mkdir()
    with cache._InstallLock(root, manifest.id):
        with pytest.raises(ModelCacheError, match="another install"):
            install(manifest, root, Fetcher())
    assert install(manifest, root, Fetcher()).downloaded == 3


def test_installer_and_resolver_read_the_pointer_identically(tmp_path):
    from runtime import local_asr_paths

    model_dir = tmp_path / "sensevoice-int8"
    model_dir.mkdir()
    for raw, expected in [
        (b"2024-07-17\n", "2024-07-17"),
        (b"2024-07-17", "2024-07-17"),
        (b"2024-07-17\r\n", "2024-07-17"),
        (b"2024-07-17\r", None),
        (b"2024-07-17\r\n\r\n", None),
        (b"r1" + b" " * 128 + b"D:outside", None),
        (b"r1 \n", None),
        (b"r1\n\n", None),
        (b"x" * 129, None),
    ]:
        (model_dir / "ACTIVE").write_bytes(raw)
        assert local_asr_paths.read_model_pointer(model_dir) == expected
        assert active_revision(tmp_path, "sensevoice-int8") == expected


def test_reparse_points_are_treated_as_links_everywhere(tmp_path, monkeypatch):
    import engine.model_cache as cache

    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    root = tmp_path / "models"
    install(manifest, root, Fetcher())
    junction = root / "sensevoice-int8"
    # Simulate a Windows junction: not a symlink, but a reparse point.
    monkeypatch.setattr(cache, "is_link", lambda path: Path(path) == junction)
    assert not verify_active(root, manifest).ok
    with pytest.raises(ModelCacheError):
        install(manifest, root, Fetcher())
    tree = junction / "2024-07-17"
    monkeypatch.setattr(cache, "is_link", lambda path: Path(path) == tree / "sub")
    assert "sub: is a link" in cache._verify_tree(tree, manifest).problems


def test_the_pointer_is_written_with_a_single_lf(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    install(manifest, tmp_path, Fetcher())
    assert (tmp_path / "sensevoice-int8" / "ACTIVE").read_bytes() == b"2024-07-17\n"


def test_hugging_face_cdn_redirects_are_allowed():
    from engine.model_cache import redirect_allowed

    for host in ("us.aws.cdn.hf.co", "eu.aws.cdn.hf.co", "cdn-lfs.hf.co", "cas-bridge.xethub.hf.co"):
        assert redirect_allowed(f"https://{host}/repos/x"), host
    for bad in ("https://evil.cdn.hf.co.example/x", "https://aws.cdn.hf.co.evil/x", "http://us.aws.cdn.hf.co/x"):
        assert not redirect_allowed(bad), bad


# ---------------------------------------------------------------- T32


def test_offline_import_installs_from_a_local_folder_and_verifies(tmp_path):
    from engine.model_cache import local_fetcher

    folder = tmp_path / "usb"
    (folder / "sub").mkdir(parents=True)
    for path, data in FILES.items():
        (folder / path.rsplit("/", 1)[-1]).write_bytes(data)
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    result = install(manifest, tmp_path / "models", local_fetcher(folder))
    assert result.downloaded == 3 and verify_active(tmp_path / "models", manifest).ok

    # A corrupted or truncated copy is refused and nothing is activated.
    (folder / "tokens.txt").write_bytes(b"corrupt")
    other = tmp_path / "models2"
    with pytest.raises(ModelCacheError):
        install(manifest, other, local_fetcher(folder))
    assert active_revision(other, "sensevoice-int8") is None
    (folder / "tokens.txt").unlink()
    with pytest.raises(ModelCacheError, match="offline folder"):
        install(manifest, other, local_fetcher(folder))


def test_offline_import_refuses_links_in_the_folder(tmp_path):
    from engine.model_cache import local_fetcher

    folder = tmp_path / "usb"
    folder.mkdir()
    real = tmp_path / "real.txt"
    real.write_bytes(FILES["tokens.txt"])
    (folder / "tokens.txt").symlink_to(real)
    for path, data in FILES.items():
        name = path.rsplit("/", 1)[-1]
        if name != "tokens.txt":
            (folder / name).write_bytes(data)
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    with pytest.raises(ModelCacheError):
        install(manifest, tmp_path / "models", local_fetcher(folder))


def test_identical_files_are_reused_instead_of_downloaded(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    root = tmp_path / "models"
    install(manifest, root, Fetcher())
    # A new revision sharing two files downloads only the changed one.
    changed = dict(FILES, **{"tokens.txt": b"a\nb\nc\n"})
    newer = ModelManifest.parse(json.dumps(manifest_dict(files=changed, revision="2024-08-01")))
    fetcher = Fetcher(files=changed)
    result = install(newer, root, fetcher)
    assert result.downloaded == 1 and fetcher.requests[0].endswith("/tokens.txt")
    assert result.reused_files == 2
    assert verify_active(root, newer).ok


def test_a_legacy_unverified_folder_is_adopted_only_when_it_matches(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    legacy = tmp_path / "legacy"
    (legacy / "sub").mkdir(parents=True)
    for path, data in FILES.items():
        (legacy / path).write_bytes(data)
    fetcher = Fetcher()
    result = install(manifest, tmp_path / "models", fetcher, reuse_dirs=[legacy])
    assert result.downloaded == 0 and result.reused_files == 3 and fetcher.requests == []
    assert verify_active(tmp_path / "models", manifest).ok
    assert (legacy / "tokens.txt").exists(), "the legacy folder is copied, never moved"

    # A damaged legacy file is not adopted: that file is downloaded instead.
    (legacy / "tokens.txt").write_bytes(b"damaged")
    fetcher = Fetcher()
    result = install(manifest, tmp_path / "other", fetcher, reuse_dirs=[legacy])
    assert result.downloaded == 1 and fetcher.requests[0].endswith("/tokens.txt")


def test_an_archive_source_is_not_downloaded_when_every_file_is_already_here(tmp_path):
    blob = _archive(good_members())
    manifest = archive_manifest(blob)
    legacy = tmp_path / "legacy"
    (legacy / "sub").mkdir(parents=True)
    for path, data in FILES.items():
        (legacy / path).write_bytes(data)
    fetcher = Fetcher(files={"bundle.tar.bz2": blob})
    result = install(manifest, tmp_path / "models", fetcher, reuse_dirs=[legacy])
    assert fetcher.requests == [] and result.reused_files == 3
    assert verify_active(tmp_path / "models", manifest).ok


def test_reuse_and_offline_import_refuse_links_anywhere_in_the_path(tmp_path):
    from engine.model_cache import local_fetcher

    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    for path, data in FILES.items():
        (real / path).write_bytes(data)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "sub").symlink_to(real / "sub", target_is_directory=True)
    for path, data in FILES.items():
        if "/" not in path:
            (legacy / path).write_bytes(data)
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    fetcher = Fetcher()
    install(manifest, tmp_path / "models", fetcher, reuse_dirs=[legacy])
    # sub/config.json sits behind a linked directory: downloaded, not reused.
    assert [u.rsplit("/", 1)[-1] for u in fetcher.requests] == ["config.json"]

    offline = tmp_path / "offline-link"
    offline.symlink_to(real, target_is_directory=True)
    with pytest.raises(ModelCacheError, match="offline folder"):
        install(manifest, tmp_path / "models2", local_fetcher(offline))


def test_staging_and_retired_folders_are_never_reuse_sources(tmp_path):
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    root = tmp_path / "models"
    model_dir = root / "sensevoice-int8"
    hidden = model_dir / ".retired-abc"
    (hidden / "sub").mkdir(parents=True)
    for path, data in FILES.items():
        (hidden / path).write_bytes(data)
    fetcher = Fetcher()
    install(manifest, root, fetcher, reuse_dirs=[hidden, model_dir])
    assert len(fetcher.requests) == 3


@pytest.mark.skipif(os.name == "nt", reason="FIFOs are POSIX")
def test_a_fifo_in_a_reuse_folder_never_hangs_the_install(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    os.mkfifo(legacy / "tokens.txt")
    manifest = ModelManifest.parse(json.dumps(manifest_dict()))
    fetcher = Fetcher()
    install(manifest, tmp_path / "models", fetcher, reuse_dirs=[legacy])
    assert len(fetcher.requests) == 3
