import base64
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0,str(ROOT/'scripts/ci'))
spec = importlib.util.spec_from_file_location('observer',ROOT/'scripts/ci/linux_dispatch_observer.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_read_only_no_config_rejects_live_job(monkeypatch):
    run=dict(id=123,run_attempt=1,event='push',path=m.WORKFLOW,head_sha='a'*40)
    requests=[]
    def api(path):
        requests.append(path)
        if '/contents/' in path:
            return {'content':base64.b64encode(b'workflow').decode()}
        if '/attempts/' in path:
            return {'jobs':[dict(id=7,name='windows-tauri-test')]}
        return {'workflow_runs':[run] if 'status=in_progress' in path else []}
    monkeypatch.setattr(m,'api',api)
    value=m.snapshot({})
    assert value['production_enabled'] is False
    assert value['paid_fallback_minutes']==0
    assert value['jobs'][0]['reason']=='routing_disabled'
    assert not any('generate-jitconfig' in path for path in requests)


def test_run_pagination_bound_fails_closed(monkeypatch):
    monkeypatch.setattr(m,'api',lambda _: {'workflow_runs':[{'path':'other'}]*100})
    with pytest.raises(RuntimeError,match='pagination'):
        m.snapshot({})


def test_atomic_receipt_and_symlink_rejection(tmp_path):
    target=tmp_path/'snapshot.json'
    m.atomic_write(target,{'production_enabled':False})
    assert target.stat().st_mode & 0o777 == 0o600
    link=tmp_path/'link.json'
    link.symlink_to(target)
    with pytest.raises(RuntimeError,match='symlink'):
        m.atomic_write(link,{'production_enabled':True})


def test_config_invalid_and_symlink_rejected(tmp_path):
    path=tmp_path/'config.json'
    path.write_text('[]')
    with pytest.raises(ValueError):
        m.trusted_config(path)
    link=tmp_path/'link.json'
    link.symlink_to(path)
    with pytest.raises(RuntimeError):
        m.trusted_config(link)
