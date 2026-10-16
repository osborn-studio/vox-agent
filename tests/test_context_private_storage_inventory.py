import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.inspect_context_private_storage import InventoryError, inspect_storage


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    for name in ("Inputs", "Drafts", "Requests"):
        (root / name).mkdir(mode=0o700)
    (root / "Requests/Archived").mkdir(mode=0o700)
    return root, tmp_path


def cipher(directory, *, name=None, data=b"not authenticated; never read"):
    target = directory / (name or "a" * 64 + ".v1.aes")
    target.write_bytes(data)
    target.chmod(0o600)
    return target


def test_operator_advice_preserves_unknown_files_and_never_authorizes_cleanup(vault):
    root, boundary = vault
    orphan = cipher(root / "Inputs", name=".input-orphan")
    foreign = cipher(root / "Inputs", name="foreign-private-name")
    unsafe = cipher(root / "Inputs")
    unsafe.chmod(0o644)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (orphan, foreign, unsafe)}
    result = inspect_storage(root, boundary)
    actions = {x["store"]: x["codes"] for x in result["operatorActions"]}
    assert actions["inputs"] == ["PRESERVE_UNSAFE_ENTRIES_FOR_OWNER_REVIEW", "PRESERVE_UNKNOWN_AND_TEMP_ENTRIES"]
    assert result["mutationAuthorization"] == "NONE"
    assert before == {p: (p.read_bytes(), p.stat().st_mode) for p in before}
    assert "foreign-private-name" not in json.dumps(result)


def test_truncated_scan_advice_cannot_claim_free_capacity(vault):
    root, boundary = vault
    for index in range(257):
        cipher(root / "Inputs", name=f"foreign-{index}")
    result = inspect_storage(root, boundary)
    actions = next(x["codes"] for x in result["operatorActions"] if x["store"] == "inputs")
    assert "COUNTS_INCOMPLETE_DO_NOT_ASSUME_FREE_CAPACITY" in actions
    assert "PAUSE_NEW_ALLOCATIONS_AND_PLAN_VERIFIED_EXPORT" in actions
    assert len(list((root / "Inputs").iterdir())) == 257


def test_metadata_only_aggregates_no_content_names_or_paths(vault):
    root, boundary = vault
    body = b"synthetic private plaintext-like sentinel not emitted"
    paths = [cipher(root / name, data=body) for name in ("Inputs", "Drafts", "Requests")]
    decision = cipher(root / "Requests", name="b" * 64 + ".v1.decision", data=body)
    archive = cipher(root / "Requests/Archived", name="a" * 64 + "." + "b" * 64 + ".v1.aes", data=body)
    before = {p: p.read_bytes() for p in [*paths, decision, archive]}
    result = inspect_storage(root, boundary)
    for store in result["stores"].values():
        assert store["status"] == "METADATA_ONLY"
        assert store["snapshots"] == 1
    assert result["stores"]["requests"]["decisions"] == 1
    text = json.dumps(result)
    assert body.decode() not in text and str(root) not in text and "a" * 64 not in text
    assert result["keychain"] == result["ciphertextAuthentication"] == result["recovery"] == "NOT_RUN"
    assert before == {p: p.read_bytes() for p in before}


def test_no_creation_missing_root(tmp_path):
    root = tmp_path / "absent"
    assert all(v["status"] == "NOT_FOUND" for v in inspect_storage(root, tmp_path)["stores"].values())
    assert not root.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "directory", "empty", "oversize"])
def test_unsafe_recognized_cipher_metadata(vault, kind):
    root, boundary = vault
    target = root / "Inputs" / ("a" * 64 + ".v1.aes")
    if kind == "directory":
        target.mkdir(mode=0o700)
    elif kind == "symlink":
        original = cipher(boundary)
        target.symlink_to(original)
    else:
        cipher(root / "Inputs", data=b"x" if kind != "empty" else b"")
        if kind == "hardlink":
            os.link(target, root / "Inputs/foreign-link")
        elif kind == "public":
            target.chmod(0o644)
        elif kind == "oversize":
            with target.open("wb") as stream:
                stream.truncate(64_001)
    result = inspect_storage(root, boundary)["stores"]["inputs"]
    assert result["status"] == "ACTION_REQUIRED" and result["unsafeCipherEntries"] == 1
    assert target.exists() or target.is_symlink()


@pytest.mark.parametrize("directory", ["Inputs", "Drafts", "Requests", "Requests/Archived"])
def test_private_directory_symlink_refused_without_traversal(vault, directory):
    root, boundary = vault
    path = root / directory
    if directory == "Requests":
        (path / "Archived").rmdir()
    path.rmdir()
    outside = boundary / "outside"
    outside.mkdir(mode=0o700)
    cipher(outside)
    path.symlink_to(outside, target_is_directory=True)
    results = inspect_storage(root, boundary)["stores"]
    key = {"Inputs": "inputs", "Drafts": "drafts", "Requests": "requests", "Requests/Archived": "requestArchives"}[directory]
    assert results[key]["status"] == "UNSAFE" and "snapshots" not in results[key]


def test_stream_cap_reports_lower_bound_and_leaves_all_entries(vault):
    root, boundary = vault
    for index in range(300):
        (root / "Drafts" / f"foreign-{index}").touch(mode=0o600)
    value = inspect_storage(root, boundary)["stores"]["drafts"]
    assert value["scanTruncated"] and value["entriesSeen"] == 257
    assert value["foreignEntries"] == 256 and value["newEntryCapacityReached"]
    assert len(list((root / "Drafts").iterdir())) == 300


def test_exact_cap_and_orphans_are_not_cleaned(vault):
    root, boundary = vault
    for index in range(256):
        (root / "Requests" / f".request-orphan-{index}").touch(mode=0o600)
    value = inspect_storage(root, boundary)["stores"]["requests"]
    assert value["scanTruncated"] and value["entriesSeen"] == 257  # Archived also consumes one entry.
    assert value["orphanTemps"] in (255, 256)  # Directory order is intentionally not sorted.
    assert len(list((root / "Requests").iterdir())) == 257


def test_newline_name_is_foreign_and_not_cipher(vault):
    root, boundary = vault
    cipher(root / "Drafts", name="a" * 64 + ".v1.aes\n")
    value = inspect_storage(root, boundary)["stores"]["drafts"]
    assert value["snapshots"] == 0 and value["foreignEntries"] == 1
    assert value["status"] == "ACTION_REQUIRED"


def test_rejects_unsafe_boundary_and_root_ancestor(tmp_path):
    with pytest.raises(InventoryError, match="INVALID_BOUNDARY"):
        inspect_storage(tmp_path, tmp_path)
    root = tmp_path / "public" / "private"
    root.parent.mkdir(mode=0o777)
    root.parent.chmod(0o777)
    with pytest.raises(InventoryError, match="UNSAFE_ANCESTOR"):
        inspect_storage(root, tmp_path)
    assert not root.exists()


def test_no_cipher_reads_or_write_open_flags(vault, monkeypatch):
    root, boundary = vault
    cipher(root / "Inputs")
    real_open = os.open
    seen = []

    def checked_open(path, flags, *args, **kwargs):
        seen.append(flags)
        assert not flags & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR)
        assert flags & os.O_DIRECTORY
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("inventory read ciphertext"))
    assert inspect_storage(root, boundary)["stores"]["inputs"]["snapshots"] == 1
    assert len(seen) == 4


def test_cli_invalid_and_healthy_json_codes(vault):
    root, boundary = vault
    script = Path(__file__).parents[1] / "scripts/inspect_context_private_storage.py"
    okay = subprocess.run([sys.executable, str(script), "--root", str(root), "--boundary", str(boundary)], capture_output=True, text=True)
    assert okay.returncode == 0 and json.loads(okay.stdout)["version"] == 1
    bad = subprocess.run([sys.executable, str(script), "--root", str(root), "--boundary", str(root)], capture_output=True, text=True)
    assert bad.returncode == 2 and json.loads(bad.stdout)["code"] == "INVALID_BOUNDARY"
    assert str(root) not in bad.stdout and not bad.stderr


def test_decisions_only_recognized_in_requests(vault):
    root, boundary = vault
    for name in ("Inputs", "Drafts", "Requests"):
        cipher(root / name, name="b" * 64 + ".v1.decision")
    stores = inspect_storage(root, boundary)["stores"]
    assert stores["requests"]["decisions"] == 1
    for name in ("inputs", "drafts"):
        assert stores[name]["decisions"] == 0
        assert stores[name]["foreignEntries"] == 1


def test_capacity_and_incomplete_counts(vault):
    root, boundary = vault
    for index in range(64):
        cipher(root / "Inputs", name=f"{index:064x}.v1.aes", data=b"x" * 62500)
    value = inspect_storage(root, boundary)["stores"]["inputs"]
    assert value["snapshotCapacityReached"] and value["byteCapacityReached"]
    assert value["countsComplete"]
    for index in range(300):
        (root / "Drafts" / str(index)).touch(mode=0o600)
    assert not inspect_storage(root, boundary)["stores"]["drafts"]["countsComplete"]
