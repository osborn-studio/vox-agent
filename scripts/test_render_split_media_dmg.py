"""Synthetic renderer transitions; no real mounting in these pytest cases."""
import json
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import render_split_media_dmg as module  # noqa: E402


def fixture(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    online = root / 'Online.app'
    binary = online / 'Contents/MacOS/VoxLog'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'fixture executable, not run')
    binary.chmod(0o755)
    stage = root / 'staging'
    stage.mkdir(mode=0o700)
    shutil.copytree(online, stage / 'Vox Agent.app')
    assets = stage / 'OfflineAssets/model'
    assets.mkdir(parents=True)
    (assets / 'config.json').write_bytes(b'fixture asset')
    (stage / 'STAGING-VERIFIED.json').write_text(json.dumps({'status': 'STAGING_VERIFIED_NOT_RELEASED', 'app_inventory_sha256': hashlib.sha256(json.dumps(module.inventory(stage / 'Vox Agent.app'), sort_keys=True).encode()).hexdigest(), 'model_file_sha256': module.assets_inventory(stage / 'OfflineAssets')}))
    return online, stage, root / 'new.dmg'


def runner(stage, fault=None):
    def run(command, **kwargs):
        operation = command[1]
        if operation == 'create':
            Path(command[-1]).write_bytes(b'fixture image')
        elif operation == 'attach':
            mount = Path(command[command.index('-mountpoint') + 1])
            shutil.copytree(stage, mount, dirs_exist_ok=True)
            if fault == 'assets':
                (mount / 'OfflineAssets/model/config.json').write_bytes(b'changed')
            if fault == 'attach':
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]}))
        elif fault == 'detach':
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout=b'')
    return run


def test_publish_only_verified_image(tmp_path, monkeypatch):
    online, stage, output = fixture(tmp_path)
    monkeypatch.setattr(module.subprocess, 'run', runner(stage))
    report = module.render(stage, online, output)
    assert report['mounted_app_and_model_snapshot_verified'] and not report['signed_release']
    assert output.read_bytes() == b'fixture image'
    assert not list(tmp_path.glob('.split-dmg-*'))
    with pytest.raises(ValueError):
        module.render(stage, online, output)
    assert output.read_bytes() == b'fixture image'


@pytest.mark.parametrize('fault', ['assets', 'attach', 'detach'])
def test_mount_failure_does_not_publish_or_delete_sources(tmp_path, monkeypatch, fault):
    online, stage, output = fixture(tmp_path)
    monkeypatch.setattr(module.subprocess, 'run', runner(stage, fault))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.render(stage, online, output)
    assert not output.exists()
    assert (stage / 'OfflineAssets/model/config.json').read_bytes() == b'fixture asset'
    assert online.exists()
    if fault == 'detach':
        assert len(list(tmp_path.glob('.split-dmg-*'))) == 1
    else:
        assert not list(tmp_path.glob('.split-dmg-*'))


@pytest.mark.parametrize('change', ['app', 'model'])
def test_stale_completion_marker_is_rejected_before_mount(tmp_path, monkeypatch, change):
    online, stage, output = fixture(tmp_path)
    if change == 'model':
        (stage / 'OfflineAssets/model/config.json').write_bytes(b'changed')
    else:
        for root in [online, stage / 'Vox Agent.app']:
            (root / 'Contents/MacOS/VoxLog').write_bytes(b'both copies changed')
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must reject before image operations'))
    with pytest.raises(ValueError):
        module.render(stage, online, output)
    assert not output.exists()
