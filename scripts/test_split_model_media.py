"""Isolated split-media producer tests; no real App/model or signing."""
import json
import hashlib
from pathlib import Path
import sys
import shutil
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import build_split_model_media as module  # noqa: E402


def fixture(tmp_path):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    app = parent / 'Source.app'
    executables = app / 'Contents/MacOS'
    executables.mkdir(parents=True)
    for name in ['VoxLog', 'qwen3-asr-server']:
        (executables / name).write_bytes(b'synthetic executable')
        (executables / name).chmod(0o755)
    resources = app / 'Contents/Resources'
    resources.mkdir()
    (resources / 'QWEN_MODEL_CATALOG.json').write_text(json.dumps({'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'}))
    licenses = resources / 'QwenRuntimeLicenses'
    licenses.mkdir()
    (licenses / 'LICENSE.txt').write_bytes(b'fixture notices')
    record = {'status': 'PREPARED_REQUIRES_SIGNING', 'helper_sha256': hashlib.sha256(b'synthetic executable').hexdigest(),
              'model_identity': {'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'},
              'notice_file_sha256': {'LICENSE.txt': hashlib.sha256(b'fixture notices').hexdigest()}}
    (resources / 'QWEN_RUNTIME_PREPARED.json').write_text(json.dumps(record))
    (parent / 'fixture-cli').write_bytes(b'fixture')
    (parent / 'fixture-cli').chmod(0o755)
    model = parent / 'original-model'
    model.mkdir(mode=0o700)
    (model / 'config.json').write_bytes(b'fixture')
    return app, model, parent / 'media'


def media_stub(command, **kwargs):
    if command[0] == '/usr/bin/ditto':
        shutil.copytree(Path(command[-2]), Path(command[-1]), symlinks=True)
        return SimpleNamespace(returncode=0, stdout=b'')
    assets = Path(command[2])
    staged = assets / 'verified-model'
    staged.mkdir(mode=0o700)
    (staged / 'config.json').write_bytes(b'fixture')
    return SimpleNamespace(returncode=0, stdout=json.dumps({'operation': 'media', 'verified_path': str(staged),
                                                          'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'}))


def test_unchanged_app_and_external_model(tmp_path, monkeypatch):
    app, model, output = fixture(tmp_path)
    monkeypatch.setattr(module.subprocess, 'run', media_stub)
    report = module.build(app, model, output, app.parent / 'fixture-cli')
    module.verify_equivalence(app, output / 'Vox Agent.app')
    assert report['model'].startswith('OfflineAssets/')
    assert not (output / 'Vox Agent.app/Contents/Resources/Models').exists()
    assert (output / 'STAGING-VERIFIED.json').exists()
    assert (model / 'config.json').read_bytes() == b'fixture'
    with pytest.raises(FileExistsError):
        module.build(app, model, output, app.parent / 'fixture-cli')


@pytest.mark.parametrize('fault', ['cli', 'receipt', 'copy-change', 'catalog', 'extra-assets'])
def test_incomplete_is_retained_without_success_marker(tmp_path, monkeypatch, fault, capsys):
    app, model, output = fixture(tmp_path)

    def changed(command, **kwargs):
        if command[0] == '/usr/bin/ditto':
            return media_stub(command)
        if fault == 'cli':
            return SimpleNamespace(returncode=1, stderr='[integrity] failed')
        result = media_stub(command)
        if fault == 'receipt':
            result.stdout = '{}'
        elif fault == 'catalog':
            receipt = json.loads(result.stdout)
            receipt['digest'] = 'wrong-revision'
            result.stdout = json.dumps(receipt)
        elif fault == 'extra-assets':
            (output / 'OfflineAssets/unexpected').write_bytes(b'extra')
        else:
            (output / 'Vox Agent.app/Contents/MacOS/VoxLog').write_bytes(b'changed')
        return result

    monkeypatch.setattr(module.subprocess, 'run', changed)
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        module.build(app, model, output, app.parent / 'fixture-cli')
    assert output.exists() and not (output / 'STAGING-VERIFIED.json').exists()
    assert 'Incomplete staging retained' in capsys.readouterr().err
    assert (app / 'Contents/MacOS/VoxLog').read_bytes() == b'synthetic executable'
    assert (model / 'config.json').read_bytes() == b'fixture'


def test_missing_runtime_precondition_creates_nothing(tmp_path):
    app, model, output = fixture(tmp_path)
    (app / 'Contents/MacOS/qwen3-asr-server').unlink()
    with pytest.raises(FileNotFoundError):
        module.build(app, model, output, app.parent / 'fixture-cli')
    assert not output.exists()


@pytest.mark.parametrize('fault', ['missing', 'status', 'identity', 'helper', 'notice', 'extra-notice', 'catalog-list'])
def test_runtime_record_rejected_before_output(tmp_path, fault):
    app, model, output = fixture(tmp_path)
    record_path = app / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json'
    record = json.loads(record_path.read_text())
    if fault == 'missing':
        record_path.unlink()
    elif fault == 'status':
        record['status'] = 'incomplete'
    elif fault == 'identity':
        record['model_identity']['digest'] = 'wrong'
    elif fault == 'helper':
        (app / 'Contents/MacOS/qwen3-asr-server').write_bytes(b'changed helper')
    elif fault == 'notice':
        (app / 'Contents/Resources/QwenRuntimeLicenses/LICENSE.txt').write_bytes(b'changed notice')
    elif fault == 'extra-notice':
        (app / 'Contents/Resources/QwenRuntimeLicenses/NOTICE.txt').write_bytes(b'extra')
    else:
        (app / 'Contents/Resources/QWEN_MODEL_CATALOG.json').write_text(json.dumps(['model_id', 'revision', 'digest']))
    if fault in {'status', 'identity'}:
        record_path.write_text(json.dumps(record))
    with pytest.raises((ValueError, FileNotFoundError)):
        module.build(app, model, output, app.parent / 'fixture-cli')
    assert not output.exists()
