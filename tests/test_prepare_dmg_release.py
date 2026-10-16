"""Release preflight must not rebuild a manually verified release."""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    'prepare_dmg_release', Path(__file__).parents[1] / 'scripts/prepare_dmg_release.py'
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def tagged(name='v4.2.2'):
    return {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF_TYPE': 'tag', 'GITHUB_REF_NAME': name, 'GITHUB_REPOSITORY': 'team/app'}


@pytest.mark.parametrize('draft', [True, False])
def test_existing_release_on_later_page_is_preserved(draft):
    pages = [[{'tag_name': 'v4.0.0'}], [{'tag_name': 'v4.2.2', 'draft': draft}]]
    with patch.object(module.subprocess, 'run') as run:
        run.return_value.stdout = json.dumps(pages)
        assert module.prepare(tagged()) == {'version': '4.2.2', 'skip': 'true'}


def test_new_tag_drives_app_version():
    with patch.object(module.subprocess, 'run') as run:
        run.return_value.stdout = '[[]]'
        assert module.prepare(tagged('v4.3.0')) == {'version': '4.3.0', 'skip': 'false'}


def test_api_failure_fails_closed():
    with patch.object(module.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'gh')):
        with pytest.raises(subprocess.CalledProcessError):
            module.prepare(tagged())


@pytest.mark.parametrize('tag', ['v4.2.2-rc1', 'v4.2', 'v4.2.2\nskip=false', 'v$(id)', 'main'])
def test_invalid_tag_never_queries_api(tag):
    with patch.object(module.subprocess, 'run') as run:
        with pytest.raises(ValueError):
            module.prepare(tagged(tag))
        run.assert_not_called()


def test_manual_build_requires_explicit_version_and_no_api():
    with patch.object(module.subprocess, 'run') as run:
        assert module.prepare({'RELEASE_VERSION': '4.3.1'}) == {'version': '4.3.1', 'skip': 'false'}
        with pytest.raises(ValueError):
            module.prepare({})
        run.assert_not_called()


def test_manual_dispatch_on_tag_uses_input_without_publishing_lookup():
    env = {**tagged(), 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'RELEASE_VERSION': '4.3.1'}
    with patch.object(module.subprocess, 'run') as run:
        assert module.prepare(env) == {'version': '4.3.1', 'skip': 'false'}
        run.assert_not_called()
