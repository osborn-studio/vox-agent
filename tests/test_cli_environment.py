import os
import shutil
import subprocess

from engine.cli_environment import cli_environment


def test_finder_environment_runs_cli_and_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr('engine.cli_environment.sys.platform', 'darwin')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    directory = tmp_path / '.local/bin'
    directory.mkdir(parents=True)
    interpreter = directory / 'vox-test-interpreter'
    interpreter.write_text('#!/bin/sh\nprintf "resolved"\n')
    interpreter.chmod(0o700)
    executable = directory / 'vox-test-cli'
    executable.write_text('#!/usr/bin/env vox-test-interpreter\n')
    executable.chmod(0o700)
    assert shutil.which('vox-test-cli') is None
    env = cli_environment()
    result = subprocess.run(['vox-test-cli'], env=env, capture_output=True, check=True)
    assert result.stdout == b'resolved'
    assert os.environ['PATH'] == '/usr/bin:/bin'


def test_preserves_precedence_and_does_not_duplicate(monkeypatch):
    monkeypatch.setattr('engine.cli_environment.sys.platform', 'darwin')
    monkeypatch.setenv('PATH', '/custom/bin:/opt/homebrew/bin:/usr/bin')
    env = cli_environment()
    assert env['PATH'].startswith('/custom/bin:/opt/homebrew/bin:/usr/bin:')
    assert env['PATH'].count('/opt/homebrew/bin') == 1


def test_other_platform_unchanged(monkeypatch):
    monkeypatch.setattr('engine.cli_environment.sys.platform', 'linux')
    assert cli_environment() == dict(os.environ)


def test_startup_diagnostics_use_same_search_path(monkeypatch, tmp_path):
    from engine.config_validator import ConfigValidator, ValidationResult
    monkeypatch.setattr('engine.cli_environment.sys.platform', 'darwin')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    directory = tmp_path / '.local/bin'
    directory.mkdir(parents=True)
    executable = directory / 'claude'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o700)
    result = ValidationResult(valid=True)
    ConfigValidator()._check_cli_tools(result)
    assert 'claude' in result.checks[0]['detail']
