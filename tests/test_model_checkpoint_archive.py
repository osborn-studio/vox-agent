import fcntl
import json
import os
import select
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import archive_model_checkpoints as archive


@pytest.fixture
def storage(tmp_path):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    root = parent / archive.CONTAINER
    root.mkdir(mode=0o700)
    for name in [".admission.lock", ".qwen-checkpoint-" + "a" * 64 + ".lock"]:
        (root / name).touch(mode=0o600)
    quarantine = root / ".quarantine-00000000-0000-0000-0000-000000000001"
    quarantine.write_bytes(b"corrupt-but-preserve")
    quarantine.chmod(0o600)
    retained = root / ".retained-job-00000000-0000-0000-0000-000000000002"
    retained.mkdir(mode=0o700)
    (retained / "partial").write_bytes(b"crash-residue-preserve")
    (retained / "partial").chmod(0o600)
    return parent, root, [quarantine, retained]


def plan(storage):
    parent, _, paths = storage
    return archive.preview(str(parent), [archive.opaque(p.name) for p in paths])


def test_preview_and_archive_preserve_bytes(storage):
    parent, root, paths = storage
    before = list(root.iterdir())
    preview = plan(storage)
    assert list(root.iterdir()) == before
    report = archive.archive(str(parent), preview)
    assert report["status"] == "archived_not_deleted"
    destination = parent / report["archive"]
    assert (destination / paths[0].name).read_bytes() == b"corrupt-but-preserve"
    assert (destination / paths[1].name / "partial").read_bytes() == b"crash-residue-preserve"
    assert not paths[0].exists() and not paths[1].exists()
    assert len(list(root.iterdir())) == 2  # Both locks remain in place.
    intent = json.loads((destination / "receipt.json").read_text())
    assert intent["plan"] == preview
    assert len(intent["selectedNames"]) == 2
    assert (destination / "receipt.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("lock", [".admission.lock", ".qwen-checkpoint-" + "a" * 64 + ".lock"])
def test_active_download_lease_refuses_without_archive(storage, lock):
    parent, root, paths = storage
    preview = plan(storage)
    fd = os.open(root / lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(archive.ArchiveError, match="download_busy"):
            archive.archive(str(parent), preview)
    finally:
        os.close(fd)
    assert all(p.exists() for p in paths)
    assert list(parent.iterdir()) == [root]


def test_stale_payload_and_roster_refused(storage):
    parent, root, paths = storage
    preview = plan(storage)
    paths[0].write_bytes(b"changed")
    with pytest.raises(archive.ArchiveError, match="preview_stale"):
        archive.archive(str(parent), preview)
    preview = plan(storage)
    (root / "unexpected").touch(mode=0o600)
    with pytest.raises(archive.ArchiveError, match="preview_stale"):
        archive.archive(str(parent), preview)
    assert list(parent.iterdir()) == [root]


@pytest.mark.parametrize("name", [".qwen-checkpoint-" + "a" * 64, "unknown", ".quarantine-bad"])
def test_unapproved_names_are_not_archivable(storage, name):
    parent, root, _ = storage
    (root / name).touch(mode=0o600)
    with pytest.raises(archive.ArchiveError, match="selected_entry_not_preserved"):
        archive.preview(str(parent), [archive.opaque(name)])


def test_partial_failure_preserves_archive_and_intent(storage, monkeypatch):
    parent, _, paths = storage
    preview = plan(storage)
    original = archive.rename_exclusive
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(28, "no space")
        original(*args)

    monkeypatch.setattr(archive, "rename_exclusive", fail_second)
    with pytest.raises(archive.ArchiveError) as error:
        archive.archive(str(parent), preview)
    recovery = error.value.recovery
    assert recovery["status"] == "unconfirmed" and len(recovery["moved"]) == 1
    destination = parent / recovery["archive"]
    assert (destination / paths[0].name).read_bytes() == b"corrupt-but-preserve"
    assert paths[1].is_dir()
    assert (destination / "receipt.json").is_file()


def test_exclusive_rename_never_overwrites(storage):
    parent, root, paths = storage
    destination = parent / "destination"
    destination.mkdir(mode=0o700)
    collision = destination / paths[0].name
    collision.write_bytes(b"keep-destination")
    src_fd, dst_fd = os.open(root, archive.FLAGS), os.open(destination, archive.FLAGS)
    try:
        with pytest.raises(OSError):
            archive.rename_exclusive(src_fd, paths[0].name, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    assert paths[0].exists() and collision.read_bytes() == b"keep-destination"


def test_missing_lock_is_not_recreated(storage):
    parent, root, _ = storage
    preview = plan(storage)
    (root / ".admission.lock").unlink()
    with pytest.raises(archive.ArchiveError):
        archive.archive(str(parent), preview)
    assert not (root / ".admission.lock").exists()
    assert list(parent.iterdir()) == [root]


def test_private_plan_file_and_schema(storage):
    parent, _, _ = storage
    preview = plan(storage)
    path = parent / "plan.json"
    path.write_text(json.dumps(preview))
    path.chmod(0o600)
    assert archive.read_plan(str(path)) == preview
    path.chmod(0o644)
    with pytest.raises(archive.ArchiveError, match="unsafe_plan_file"):
        archive.read_plan(str(path))
    with pytest.raises(archive.ArchiveError, match="invalid_plan"):
        archive.archive(str(parent), {**preview, "delete": True})


def test_no_job_lease_provenance_refuses(storage):
    parent, root, paths = storage
    (root / (".qwen-checkpoint-" + "a" * 64 + ".lock")).unlink()
    preview = plan(storage)
    with pytest.raises(archive.ArchiveError, match="job_lease_provenance_unconfirmed"):
        archive.archive(str(parent), preview)
    assert list(parent.iterdir()) == [root] and all(p.exists() for p in paths)


def test_unknown_lease_refuses(storage):
    parent, root, paths = storage
    (root / "future-runtime.lock").touch(mode=0o600)
    preview = plan(storage)
    with pytest.raises(archive.ArchiveError, match="unknown_lock_provenance"):
        archive.archive(str(parent), preview)
    assert list(parent.iterdir()) == [root] and all(p.exists() for p in paths)


def test_deep_json_reports_stable_failure(storage):
    parent, root, paths = storage
    file = parent / "deep-plan.json"
    file.write_text("[" * 2000 + "0" + "]" * 2000)
    file.chmod(0o600)
    with pytest.raises(archive.ArchiveError, match="plan_nesting_invalid"):
        archive.read_plan(str(file))
    result = subprocess.run(
        [sys.executable, str(Path(archive.__file__)), "--parent", str(parent),
         "--apply-plan", str(file)], text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["error"] == "plan_nesting_invalid"
    assert all(p.exists() for p in paths) and not list(parent.glob("VoxAgentModelRecovery-*"))


@pytest.mark.parametrize("value", ["[" * 2000, '\\"' + "{" * 2000])
def test_depth_limit_ignores_json_strings(storage, value):
    parent, _, _ = storage
    path = parent / "string-plan.json"
    path.write_text(json.dumps({"untrusted": value}))
    path.chmod(0o600)
    assert archive.read_plan(str(path)) == {"untrusted": value}
    with pytest.raises(archive.ArchiveError, match="invalid_plan"):
        archive.archive(str(parent), archive.read_plan(str(path)))


def test_oversized_private_plan_refused(storage):
    parent, _, _ = storage
    path = parent / "large-plan.json"
    path.write_bytes(b" " * 262145)
    path.chmod(0o600)
    with pytest.raises(archive.ArchiveError, match="unsafe_plan_file"):
        archive.read_plan(str(path))


def test_invalid_selections_and_symlinks(storage):
    parent, root, _ = storage
    for selected in [[], [{}], ["x"], ["a" * 64] * 2]:
        with pytest.raises(archive.ArchiveError, match="invalid_selection"):
            archive.preview(str(parent), selected)
    (root / "bad-symlink").symlink_to(parent)
    with pytest.raises(archive.InspectionError, match="unsafe_entry"):
        plan(storage)


def test_real_process_crash_preserves_intent_and_releases_leases(storage):
    parent, root, paths = storage
    preview = plan(storage)
    code = """
import json,signal,sys
from scripts import archive_model_checkpoints as a
original=a.rename_exclusive
def stop_after_first(*args):
 original(*args)
 print('moved-one',flush=True)
 signal.pause()
a.rename_exclusive=stop_after_first
a.archive(sys.argv[1],json.loads(sys.argv[2]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(parent), json.dumps(preview)],
        cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0], "child did not reach rename"
        assert process.stdout.readline().strip() == "moved-one"
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        process.communicate(timeout=5)
    destinations = list(parent.glob("VoxAgentModelRecovery-*"))
    assert len(destinations) == 1
    assert (destinations[0] / paths[0].name).read_bytes() == b"corrupt-but-preserve"
    assert json.loads((destinations[0] / "receipt.json").read_text())["plan"] == preview
    assert paths[1].is_dir() and not paths[0].exists()
    with pytest.raises(archive.ArchiveError):
        archive.archive(str(parent), preview)  # Never replay the stale original selection.
    # A fresh, explicit remaining selection can acquire leases after the killed process.
    remaining = archive.preview(str(parent), [archive.opaque(paths[1].name)])
    report = archive.archive(str(parent), remaining)
    assert report["status"] == "archived_not_deleted"
