import fcntl
import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest

from scripts.export_context_private_snapshots import ExportError, export_snapshots


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "vault"
    root.mkdir(mode=0o700)
    (root / "Inputs").mkdir(mode=0o700)
    lock = root / "Inputs/.vault.lock"
    lock.touch(mode=0o600)
    source = root / "Inputs" / ("a" * 64 + ".v1.aes")
    source.write_bytes(b"synthetic opaque data not authenticated")
    source.chmod(0o600)
    exports = tmp_path / "exports"
    exports.mkdir(mode=0o700)
    return root, tmp_path, exports / "new", source, lock


def test_exact_opaque_copy_no_key_or_recovery_claim(storage):
    root, boundary, output, source, _ = storage
    before = source.read_bytes()
    result = export_snapshots(root, boundary, output, store="Inputs")
    assert result["status"] == "COPIED_NOT_RECOVERY_VERIFIED"
    manifest = json.loads(output.read_text())
    assert base64.b64decode(manifest["files"][0]["opaque_base64"]) == source.read_bytes() == before
    assert manifest["files"][0]["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert manifest["authentication"] == manifest["recovery"] == manifest["keychain"] == "NOT_RUN"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("case", ["symlink", "hardlink", "public", "foreign", "orphan", "too-large"])
def test_unsafe_or_unknown_source_refuses_before_output(storage, case):
    root, boundary, output, source, _ = storage
    if case == "symlink":
        source.rename(root / "original")
        source.symlink_to(root / "original")
    elif case == "hardlink":
        os.link(source, root / "second-link")
    elif case == "public":
        source.chmod(0o644)
    elif case in ("foreign", "orphan"):
        (source.parent / ("foreign" if case == "foreign" else ".input-crash")).touch(mode=0o600)
    else:
        source.write_bytes(b"x" * 64001)
    with pytest.raises(ExportError):
        export_snapshots(root, boundary, output, store="Inputs")
    assert not output.exists() and source.exists()


def test_busy_lock_never_creates_output(storage):
    root, boundary, output, _, lock = storage
    with lock.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ExportError, match="STORE_BUSY"):
            export_snapshots(root, boundary, output, store="Inputs")
    assert not output.exists()


def test_existing_output_and_inside_source_refused(storage):
    root, boundary, output, source, _ = storage
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ExportError):
        export_snapshots(root, boundary, output, store="Inputs")
    assert sentinel.read_bytes() == b"keep"
    with pytest.raises(ExportError, match="OUTPUT_INSIDE_SOURCE"):
        export_snapshots(root, boundary, root / "Inputs/new", store="Inputs")
    assert source.exists()


def test_direct_cli_only_reports_counts(storage):
    root, boundary, output, source, _ = storage
    script = __import__("pathlib").Path(__file__).parents[1] / "scripts/export_context_private_snapshots.py"
    result = subprocess.run([sys.executable, str(script), "--root", str(root), "--boundary", str(boundary),
                             "--output", str(output), "--store", "Inputs"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert source.name not in result.stdout and str(root) not in result.stdout
    assert json.loads(result.stdout)["files"] == 1


def test_empty_store_refuses(storage):
    root, boundary, output, source, _ = storage
    source.unlink()
    with pytest.raises(ExportError, match="EMPTY_STORE"):
        export_snapshots(root, boundary, output, store="Inputs")
    assert not output.exists()


def test_failed_publication_cleans_only_own_staging(storage, monkeypatch):
    root, boundary, output, source, _ = storage
    def fail(*args, **kwargs):
        raise OSError("synthetic")
    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(ExportError):
        export_snapshots(root, boundary, output, store="Inputs")
    assert not output.exists() and list(output.parent.iterdir()) == []
    assert source.exists()


def test_drafts_bundle_preserves_opaque_bytes(storage):
    root, boundary, output, source, _ = storage
    drafts = root / "Drafts"
    drafts.mkdir(mode=0o700)
    (drafts / ".vault.lock").touch(mode=0o600)
    draft = drafts / source.name
    draft.write_bytes(b"synthetic draft")
    draft.chmod(0o600)
    result = export_snapshots(root, boundary, output, store="Drafts")
    assert result["bytes"] == len(b"synthetic draft")
    assert base64.b64decode(json.loads(output.read_text())["files"][0]["opaque_base64"]) == draft.read_bytes()


def test_destination_io_occurs_after_source_lock_release(storage, monkeypatch):
    root, boundary, output, _, lock = storage
    original = os.fsync
    def assert_unlocked(fd):
        with lock.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original(fd)
    monkeypatch.setattr(os, "fsync", assert_unlocked)
    assert export_snapshots(root, boundary, output, store="Inputs")["files"] == 1


def test_publication_success_survives_staging_cleanup_failure(storage, monkeypatch):
    root, boundary, output, _, _ = storage
    def fail(*args, **kwargs):
        raise OSError('synthetic cleanup failure')
    monkeypatch.setattr(os, 'unlink', fail)
    result = export_snapshots(root, boundary, output, store='Inputs')
    assert output.is_file() and result['private_staging_cleanup_warning'] is True
    assert json.loads(output.read_text())['recovery'] == 'NOT_RUN'


def test_cleanup_failure_does_not_mask_refusal(storage, monkeypatch):
    root, boundary, output, _, _ = storage
    def fail(*args, **kwargs):
        raise OSError('synthetic')
    monkeypatch.setattr(os, 'link', fail)
    monkeypatch.setattr(os, 'unlink', fail)
    with pytest.raises(ExportError, match='IO_OR_UNSUPPORTED_PLATFORM'):
        export_snapshots(root, boundary, output, store='Inputs')
    assert not output.exists()
