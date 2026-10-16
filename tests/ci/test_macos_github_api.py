import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "macos_github_api", Path(__file__).resolve().parents[2] / "scripts/ci/macos_github_api.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
LOOKUP = "orgs/osborn-studio/actions/runners/42"


def client(status, body, exit_code=0):
    def run(command, **kwargs):
        return SimpleNamespace(returncode=exit_code,
                               stdout=f"HTTP/2.0 {status}\nContent-Type: application/json\n\n{body}",
                               stderr="credential-bearing error must never be reported")
    return m.GitHubAPI(run=run)


def test_only_direct_runner_404_is_absence():
    assert client(404, '{"message":"Not Found"}', 1)(LOOKUP) is None
    with pytest.raises(RuntimeError):
        client(404, '{}', 1)("repos/osborn-studio/osvox/actions/jobs/42")


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_failed_http_never_means_absence(status):
    with pytest.raises(RuntimeError) as error:
        client(status, '{}', 1)(LOOKUP)
    assert "credential" not in str(error.value)


def test_transport_failure_and_malformed_success_fail_closed():
    for response in [SimpleNamespace(returncode=1, stdout='', stderr='secret'),
                     SimpleNamespace(returncode=0, stdout='{}', stderr=''),
                     SimpleNamespace(returncode=0, stdout='HTTP/2.0 200\n\nnull', stderr='')]:
        with pytest.raises((ValueError, RuntimeError)):
            m.GitHubAPI(run=lambda *a, **kw: response)(LOOKUP)
    with pytest.raises(ValueError):
        client(200, 'not json')(LOOKUP)


def test_success_and_delete_204():
    assert client(200, '{"id":42}')(LOOKUP) == {"id": 42}
    assert client(204, '')(LOOKUP, method='DELETE') == {}


@pytest.mark.parametrize("endpoint,method", [
    ('repos/other/repo/actions/jobs/42', 'GET'),
    ('repos/osborn-studio/osvox/actions/runs/42/cancel', 'POST'),
    (LOOKUP, 'POST'), (LOOKUP + '?x=1', 'DELETE'),
])
def test_rejects_unapproved_endpoints_before_transport(endpoint, method):
    def run(*args, **kwargs):
        pytest.fail('Invalid endpoint reached transport')
    with pytest.raises(ValueError):
        m.GitHubAPI(run=run)(endpoint, method=method)


def test_registration_body_stays_on_stdin_and_environment_is_bounded():
    def run(command, **kwargs):
        assert command[:4] == ['/opt/homebrew/bin/gh', 'api', '--hostname', 'github.com']
        assert 'synthetic-jit' not in str(command)
        assert kwargs['input'] == '{"encoded_jit_config": "synthetic-jit"}'
        assert 'GH_TOKEN' not in kwargs['env']
        assert kwargs['timeout'] == 30
        return SimpleNamespace(returncode=0, stdout='HTTP/2.0 201\n\n{"runner":{"id":42}}', stderr='')
    assert m.GitHubAPI(run=run)(
        'orgs/osborn-studio/actions/runners/generate-jitconfig', method='POST',
        body={'encoded_jit_config': 'synthetic-jit'},
    ) == {'runner': {'id': 42}}
