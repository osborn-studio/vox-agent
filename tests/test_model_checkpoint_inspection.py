"""Metadata diagnostics must never become a cache cleanup or content reader."""

import importlib.util
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "checkpoint_inspection", Path(__file__).parents[1] / "scripts/inspect_model_checkpoints.py"
)
inspection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspection)


@pytest.fixture
def parent(tmp_path):
    path = tmp_path.resolve()
    path.chmod(0o700)
    return path


def container(parent):
    path = parent / inspection.CONTAINER
    path.mkdir(mode=0o700)
    return path


def private_file(path, text="payload-do-not-print"):
    path.write_text(text)
    path.chmod(0o600)


def test_absent_does_not_create(parent):
    assert inspection.inspect(str(parent))["present"] is False
    assert list(parent.iterdir()) == []


def test_capacity_reports_and_preserves_bytes(parent):
    root = container(parent)
    for index in range(4):
        job = root / (".qwen-checkpoint-" + f"{index:064x}")
        job.mkdir(mode=0o700)
        private_file(job / "model.bin", "abc")
        private_file(root / f".quarantine-{index}")
        retained = root / f".retained-job-{index}"
        retained.mkdir(mode=0o700)
        private_file(retained / "crash-partial", "xyz")
    private_file(root / ".admission.lock", "")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = inspection.inspect(str(parent))
    assert report["counts"] == {
        "job": 4, "retained": 4, "quarantine": 4, "lock": 1, "unknown": 0
    }
    assert all(report["capacityPressure"][key] for key in ["newMirrorJob", "quarantine", "retainedJob"])
    assert report["deletionAuthorized"] is False
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert "payload-do-not-print" not in str(report)


def test_unknown_names_opaque(parent):
    root = container(parent)
    private_file(root / "employee-content-secret-name")
    report = inspection.inspect(str(parent))
    assert report["counts"]["unknown"] == 1
    assert "employee-content" not in str(report)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public"])
def test_unsafe_entries_refused(parent, kind):
    root = container(parent)
    entry = root / "bad"
    if kind == "symlink":
        entry.symlink_to(parent)
    elif kind == "hardlink":
        source = parent / "outside"
        private_file(source)
        os.link(source, entry)
    elif kind == "fifo":
        os.mkfifo(entry, 0o600)
    else:
        private_file(entry)
        entry.chmod(0o644)
    with pytest.raises(inspection.InspectionError, match="unsafe_entry"):
        inspection.inspect(str(parent))


def test_symlink_container_and_ancestor_refused(parent):
    (parent / inspection.CONTAINER).symlink_to(parent)
    with pytest.raises(OSError):
        inspection.inspect(str(parent))
    alias = parent / "alias"
    alias.symlink_to(parent)
    with pytest.raises(OSError):
        inspection.inspect(str(alias))


def test_scan_bounded(parent):
    root = container(parent)
    for index in range(129):
        private_file(root / str(index), "")
    with pytest.raises(inspection.InspectionError, match="entry_limit_exceeded"):
        inspection.inspect(str(parent))


def test_nested_directory_refused(parent):
    root = container(parent)
    nested = root / "nested"
    nested.mkdir(mode=0o700)
    (nested / "more").mkdir(mode=0o700)
    with pytest.raises(inspection.InspectionError, match="nested_directory_unconfirmed"):
        inspection.inspect(str(parent))


def test_does_not_open_payloads(parent, monkeypatch):
    root = container(parent)
    private_file(root / "payload", "secret")
    original = os.open

    def guarded(path, flags, *args, **kwargs):
        assert flags & os.O_DIRECTORY  # Only directory descriptors may be opened.
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)
    assert inspection.inspect(str(parent))["entries"][0]["logicalBytes"] == 6


def test_relative_and_traversal_refused(parent):
    for path in ["relative", str(parent) + "/../other"]:
        with pytest.raises(inspection.InspectionError):
            inspection.inspect(path)
