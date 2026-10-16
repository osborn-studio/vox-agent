"""Synthetic read-only App payload identity checks."""
import importlib.util
from pathlib import Path
import shutil

import pytest

spec = importlib.util.spec_from_file_location('equivalence', Path(__file__).with_name('verify_app_equivalence.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def copies(tmp_path):
    online = tmp_path / 'online.app'
    binary = online / 'Contents/MacOS/VoxLog'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic executable')
    binary.chmod(0o755)
    offline = tmp_path / 'offline.app'
    shutil.copytree(online, offline)
    return online, offline


def test_same_payload_and_external_assets(tmp_path):
    online, offline = copies(tmp_path)
    assets = tmp_path / 'OfflineAssets'
    assets.mkdir()
    (assets / 'weights.safetensors').write_bytes(b'outside app')
    module.verify(online, offline)
    assert (online / 'Contents/MacOS/VoxLog').read_bytes() == b'synthetic executable'


@pytest.mark.parametrize('change', ['bytes', 'mode', 'added', 'missing', 'link'])
def test_mismatch_rejected(tmp_path, change):
    online, offline = copies(tmp_path)
    binary = offline / 'Contents/MacOS/VoxLog'
    if change == 'bytes':
        binary.write_bytes(b'changed')
    elif change == 'mode':
        binary.chmod(0o644)
    elif change == 'added':
        (offline / 'extra').write_bytes(b'added')
    elif change == 'missing':
        binary.unlink()
    else:
        (offline / 'link').symlink_to('Contents')
    with pytest.raises(ValueError):
        module.verify(online, offline)


def test_matching_internal_links_but_escape_rejected(tmp_path):
    online, offline = copies(tmp_path)
    for root in [online, offline]:
        (root / 'internal').symlink_to('Contents')
    module.verify(online, offline)
    for root in [online, offline]:
        (root / 'escape').symlink_to('../outside')
    with pytest.raises(ValueError):
        module.verify(online, offline)


def test_scan_failure_is_not_identity(tmp_path, monkeypatch):
    online, offline = copies(tmp_path)

    def fails(*args, onerror, **kwargs):
        onerror(PermissionError('synthetic scan failure'))
        yield

    monkeypatch.setattr(module.os, 'walk', fails)
    with pytest.raises(PermissionError):
        module.verify(online, offline)


def test_same_copy_is_not_two_artifact_evidence(tmp_path):
    online, _ = copies(tmp_path)
    with pytest.raises(ValueError):
        module.verify(online, online)


@pytest.mark.parametrize('change', ['same-size-bytes', 'directory-mode', 'absolute-link', 'fifo'])
def test_additional_payload_boundaries(tmp_path, change):
    import os
    online, offline = copies(tmp_path)
    if change == 'same-size-bytes':
        binary = offline / 'Contents/MacOS/VoxLog'
        binary.write_bytes(b'X' * len(binary.read_bytes()))
    elif change == 'directory-mode':
        (offline / 'Contents').chmod(0o700)
    elif change == 'absolute-link':
        for root in [online, offline]:
            (root / 'absolute').symlink_to(root / 'Contents')
    else:
        for root in [online, offline]:
            os.mkfifo(root / 'special')
    with pytest.raises(ValueError):
        module.verify(online, offline)
