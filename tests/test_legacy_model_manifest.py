import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "model_manifest", Path(__file__).parents[1] / "scripts/build_legacy_model_manifest.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_fixed_canonical_vector(tmp_path):
    (tmp_path / "config.json").touch()
    entries = module.inventory(tmp_path)
    assert (
        module.canonical_digest(entries)
        == "f54b49734a4dbbdea26e470703f398af4f5107e68dccea510e190a3ceeaa8740"
    )


def test_every_file_including_tokenizer_changes_digest(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    token = tmp_path / "tokenizer.json"
    token.write_text("v1")
    before = module.canonical_digest(module.inventory(tmp_path))
    token.write_text("v2")
    assert module.canonical_digest(module.inventory(tmp_path)) != before


@pytest.mark.parametrize("name", ["bad name", "bad.", "中文"])
def test_unsafe_names_rejected(tmp_path, name):
    (tmp_path / name).touch()
    with pytest.raises(ValueError):
        module.inventory(tmp_path)


def test_symlink_and_empty_tree_rejected(tmp_path):
    with pytest.raises(ValueError):
        module.inventory(tmp_path)
    (tmp_path / "config.json").symlink_to("/etc/hosts")
    with pytest.raises(ValueError):
        module.inventory(tmp_path)


def test_wrong_dmg_hash_never_mounts(tmp_path, monkeypatch):
    dmg = tmp_path / "old.dmg"
    dmg.write_bytes(b"not trusted")
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.inspect(dmg, "0" * 64)
    assert calls == []


def test_bundle_ancestor_symlink_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "media"
    root.mkdir()
    (root / "Vox Agent.app").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        module.contained_path(root, root / "Vox Agent.app/Contents/Info.plist")


def test_evidence_publication_never_overwrites(tmp_path):
    output = tmp_path / "evidence.json"
    module.write_evidence(output, {"runtime_trusted": False})
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        module.write_evidence(output, {"runtime_trusted": True})
    assert output.read_bytes() == before
    assert list(tmp_path.glob(".vox-evidence-*")) == []


def test_mounts_verified_private_snapshot(tmp_path, monkeypatch):
    import hashlib
    import plistlib
    from types import SimpleNamespace

    dmg = tmp_path / "original.dmg"
    dmg.write_bytes(b"published")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "attach":
            snapshot = Path(args[-1])
            assert snapshot != dmg
            assert snapshot.read_bytes() == b"published"
            dmg.write_bytes(b"changed after snapshot verification")
            mount = Path(args[-2])
            contents = mount / "Vox Agent.app/Contents"
            model = contents / "Resources/Models/Qwen3-ASR"
            model.mkdir(parents=True)
            (model / "config.json").write_text("{}")
            (contents / "Info.plist").write_bytes(
                plistlib.dumps({"CFBundleShortVersionString": "4.2.3"})
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    evidence = module.inspect(dmg, hashlib.sha256(b"published").hexdigest())
    assert evidence["source_version"] == "4.2.3"
    assert evidence["runtime_trusted"] is False
    assert calls[-1][1] == "detach"
    assert not Path(calls[0][-1]).exists()


def test_failed_detach_preserves_inspection_directory(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    dmg = tmp_path / "old.dmg"
    dmg.write_bytes(b"trusted")
    retained = tmp_path / "retained"
    retained.mkdir()
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kw: str(retained))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match="Detach failed"):
        module.inspect(dmg, hashlib.sha256(b"trusted").hexdigest())
    assert (retained / "source.dmg").read_bytes() == b"trusted"
