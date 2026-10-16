"""Prepare only synthetic helpers/notices; never execute an App."""
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import prepare_common_asr_app as module  # noqa: E402


def fixture(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    source = root / 'Source.app'
    binary = source / 'Contents/MacOS/VoxLog'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'fixture executable')
    binary.chmod(0o755)
    resources = source / 'Contents/Resources'
    resources.mkdir()
    (resources / 'QWEN_MODEL_CATALOG.json').write_text(json.dumps({'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'}))
    helper = root / 'helper'
    helper.write_bytes(b'synthetic helper NEVER EXECUTE')
    helper.chmod(0o755)
    notices = root / 'notices'
    notices.mkdir()
    (notices / 'LICENSE.txt').write_text('synthetic license fixture')

    def ditto(command, **kwargs):
        shutil.copytree(command[-2], command[-1], symlinks=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, 'run', ditto)
    return source, helper, notices, root / 'Common.app'


def test_common_app_retains_sources_and_requires_signing(tmp_path, monkeypatch):
    args = fixture(tmp_path, monkeypatch)
    report = module.prepare(*args)
    source, helper, notices, output = args
    assert report['requires_signing'] and not report['runtime_executed']
    assert not report['notice_completeness_verified']
    assert (output / 'Contents/MacOS/qwen3-asr-server').read_bytes() == helper.read_bytes()
    assert (output / 'Contents/Resources/QwenRuntimeLicenses/LICENSE.txt').read_text() == (notices / 'LICENSE.txt').read_text()
    assert not (source / 'Contents/MacOS/qwen3-asr-server').exists()
    with pytest.raises(ValueError):
        module.prepare(*args)


@pytest.mark.parametrize('fault', ['helper-link', 'helper-writable', 'notice-link', 'unexpected-notice', 'missing-catalog'])
def test_invalid_inputs_create_no_output(tmp_path, monkeypatch, fault):
    source, helper, notices, output = fixture(tmp_path, monkeypatch)
    if fault == 'helper-link':
        linked = helper.parent / 'helper-link'
        linked.symlink_to(helper)
        helper = linked
    elif fault == 'helper-writable':
        helper.chmod(0o777)
    elif fault == 'notice-link':
        (notices / 'NOTICE').symlink_to(helper)
    elif fault == 'unexpected-notice':
        (notices / 'config.json').write_bytes(b'not a license')
    else:
        (source / 'Contents/Resources/QWEN_MODEL_CATALOG.json').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        module.prepare(source, helper, notices, output)
    assert not output.exists()


@pytest.mark.parametrize('fault', ['executable', 'binary', 'catalog-list', 'catalog-empty'])
def test_notice_and_catalog_shapes(tmp_path, monkeypatch, fault):
    source, helper, notices, output = fixture(tmp_path, monkeypatch)
    if fault == 'executable':
        (notices / 'LICENSE.txt').chmod(0o755)
    elif fault == 'binary':
        (notices / 'LICENSE.txt').write_bytes(b'\x00binary')
    else:
        value = ['model_id', 'revision', 'digest'] if fault == 'catalog-list' else {'model_id': '', 'revision': 'x', 'digest': 'y'}
        (source / 'Contents/Resources/QWEN_MODEL_CATALOG.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module.prepare(source, helper, notices, output)
    assert not output.exists()


@pytest.mark.parametrize('mask', [0o002, 0o077])
def test_umask_does_not_make_additions_writable(tmp_path, monkeypatch, mask):
    import os
    args = fixture(tmp_path, monkeypatch)
    previous = os.umask(mask)
    try:
        module.prepare(*args)
    finally:
        os.umask(previous)
    assert all(item[0] == 'link' or not item[1] & 0o022 for item in module.inventory(args[-1]).values())
    assert (args[-1] / 'Contents/Resources/QwenRuntimeLicenses').stat().st_mode & 0o777 == 0o755


def test_failed_preparation_cannot_feed_split_producer(tmp_path, monkeypatch):
    from split_media_manifest import verify_runtime_preparation
    source, helper, notices, output = fixture(tmp_path, monkeypatch)
    original = module.shutil.copyfileobj
    monkeypatch.setattr(module.shutil, 'copyfileobj', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('fixture disk failure')))
    with pytest.raises(OSError):
        module.prepare(source, helper, notices, output)
    assert (output / 'Contents/MacOS/qwen3-asr-server').exists()
    assert not (output / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json').exists()
    with pytest.raises(FileNotFoundError):
        verify_runtime_preparation(output, {'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'})
    monkeypatch.setattr(module.shutil, 'copyfileobj', original)
