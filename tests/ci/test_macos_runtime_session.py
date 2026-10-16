import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    'macos_runtime_session', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_runtime_session.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_nonce_bound_stdin_transport_and_clean_close(tmp_path):
    server = tmp_path / 'server.py'
    server.write_text('import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({"nonce":r["nonce"],"value":r["op"]}),flush=True)\n')
    with m.Session([sys.executable, str(server)]) as session:
        assert session.call('inventory') == 'inventory'
        assert session.call('launch', key='10-2-30', jit='synthetic') == 'launch'
    assert session.process.poll() == 0


def test_stale_nonce_and_error_never_become_absence(tmp_path):
    for reply in ('{"nonce":"stale","value":true}', '{"status":"ERROR_FAIL_CLOSED"}'):
        server = tmp_path / 'server.py'
        server.write_text('import sys\nfor line in sys.stdin:\n print(' + repr(reply) + ',flush=True)\n')
        with m.Session([sys.executable, str(server)]) as session:
            with pytest.raises(RuntimeError):
                session.call('inventory')


def test_timeout_is_bounded(tmp_path):
    server = tmp_path / 'server.py'
    server.write_text('import time;time.sleep(10)')
    with m.Session([sys.executable, str(server)]) as session:
        with pytest.raises(RuntimeError):
            session.call('inventory', timeout=0.05)


def test_launch_timeout_leaves_lock_holder_running(tmp_path):
    server = tmp_path / 'server.py'
    server.write_text('import sys,time\nfor line in sys.stdin:\n time.sleep(0.3)\n')
    session = m.Session([sys.executable, str(server)])
    with pytest.raises(RuntimeError):
        session.call('launch', key='10-2-30', jit='synthetic', timeout=0.05)
    session.close()
    assert session.process.poll() is None
    assert session.process.wait(timeout=5) == 0
