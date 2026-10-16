import importlib.util
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

spec = importlib.util.spec_from_file_location(
    "restore_verify", Path(__file__).parents[1] / "deploy/feishu-team/verify-restore.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path, extra=None, count=1, mutation=None):
    tmp_path.chmod(0o700)
    db = tmp_path / "source.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("create table context(value text)")
        connection.execute("insert into context values('synthetic')")
    archive = tmp_path / "snapshot.tar"
    contents = {"a.sqlite": db.read_bytes(), "runtime.json": b"{}"}
    manifest = {
        "databases": count,
        "schemaVersion": 2,
        "files": {
            name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in contents.items()
        },
    }
    if mutation:
        mutation(manifest, contents)
    with tarfile.open(archive, "w") as bundle:
        for name, data in [
            ("snapshot/a.sqlite", contents["a.sqlite"]),
            ("snapshot/runtime.json", contents["runtime.json"]),
            ("snapshot/manifest.json", json.dumps(manifest).encode()),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        if extra:
            info, data = extra
            bundle.addfile(info, io.BytesIO(data))
    return archive


def test_real_sqlite_restore_is_isolated(tmp_path):
    archive = fixture(tmp_path)
    before = (tmp_path / "source.sqlite").read_bytes()
    assert module.verify(archive, tmp_path) == {
        "databases": 1,
        "integrity": "ok",
        "production_modified": False,
        "manifest_verified": True,
    }
    assert before == (tmp_path / "source.sqlite").read_bytes()
    assert not list(tmp_path.glob("restore-*"))


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate", "unknown", "count"])
def test_invalid_archive_rejected_and_plaintext_removed(tmp_path, kind):
    info = tarfile.TarInfo(
        {"traversal": "snapshot/../escape", "duplicate": "snapshot/a.sqlite"}.get(
            kind, "snapshot/extra"
        )
    )
    if kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
    archive = fixture(
        tmp_path, None if kind == "count" else (info, b""), count=2 if kind == "count" else 1
    )
    with pytest.raises(ValueError):
        module.verify(archive, tmp_path)
    assert not list(tmp_path.glob("restore-*"))


def test_database_inventory_must_match_when_supplied(tmp_path):
    archive = fixture(tmp_path)
    with pytest.raises(ValueError, match="inventory"):
        module.verify(archive, tmp_path, {"snapshot/wrong.sqlite"})
    assert not list(tmp_path.glob("restore-*"))


@pytest.mark.parametrize("change", ["hash", "size", "missing", "extra", "version", "config"])
def test_content_manifest_mismatch_rejected(tmp_path, change):
    def mutate(manifest, contents):
        if change == "hash":
            manifest["files"]["a.sqlite"]["sha256"] = "0" * 64
        elif change == "size":
            manifest["files"]["a.sqlite"]["size"] += 1
        elif change == "missing":
            del manifest["files"]["a.sqlite"]
        elif change == "extra":
            manifest["files"]["other.sqlite"] = manifest["files"]["a.sqlite"]
        elif change == "version":
            manifest["schemaVersion"] = True
        else:
            contents["runtime.json"] = b'{"enabled":false}'

    archive = fixture(tmp_path, mutation=mutate)
    with pytest.raises(ValueError, match="manifest"):
        module.verify(archive, tmp_path)
    assert not list(tmp_path.glob("restore-*"))


def test_legacy_requires_opt_in_and_expected_inventory(tmp_path):
    def legacy(manifest, contents):
        del manifest["schemaVersion"]
        del manifest["files"]

    archive = fixture(tmp_path, mutation=legacy)
    with pytest.raises(ValueError, match="legacy"):
        module.verify(archive, tmp_path)
    with pytest.raises(ValueError, match="legacy"):
        module.verify(archive, tmp_path, allow_legacy=True)
    result = module.verify(archive, tmp_path, {"snapshot/a.sqlite"}, allow_legacy=True)
    assert result["manifest_verified"] is False
