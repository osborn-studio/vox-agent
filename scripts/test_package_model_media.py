"""Synthetic package tests: no real models or network."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('package_media', Path(__file__).with_name('package_model_media.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def roots(tmp_path):
    source = tmp_path.resolve() / 'source'
    source.mkdir(mode=0o700)
    destination = tmp_path.resolve() / 'output'
    destination.mkdir(mode=0o700)
    (source / 'nested').mkdir(mode=0o700)
    (source / 'nested/config.json').write_bytes(b'synthetic bytes')
    return source, destination


def test_deterministic_archive_and_preservation(tmp_path):
    source, destination = roots(tmp_path)
    first, second = destination / 'one.zip', destination / 'two.zip'
    module.package_directory(source, first)
    module.package_directory(source, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ['nested/config.json']
        assert archive.read('nested/config.json') == b'synthetic bytes'
        assert archive.getinfo('nested/config.json').external_attr >> 16 & 0o777 == 0o600
    assert (source / 'nested/config.json').read_bytes() == b'synthetic bytes'
    with pytest.raises(FileExistsError):
        module.package_directory(source, first)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize('kind', ['file-link', 'directory-link', 'public-parent', 'dangling-output'])
def test_unsafe_paths_and_existing_artifacts(tmp_path, kind):
    source, destination = roots(tmp_path)
    output = destination / 'model.zip'
    if kind == 'file-link':
        (source / 'link').symlink_to(source / 'nested/config.json')
    elif kind == 'directory-link':
        (source / 'link').symlink_to(source / 'nested', target_is_directory=True)
    elif kind == 'public-parent':
        destination.chmod(0o755)
    else:
        output.symlink_to(destination / 'missing')
    with pytest.raises((ValueError, FileExistsError)):
        module.package_directory(source, output)
    assert not output.exists()
    if kind == 'dangling-output':
        assert output.is_symlink()
    assert (source / 'nested/config.json').read_bytes() == b'synthetic bytes'


def test_write_failure_removes_only_new_archive(tmp_path, monkeypatch):
    source, destination = roots(tmp_path)
    original = destination / 'retained.zip'
    original.write_bytes(b'old artifact')
    output = destination / 'failed.zip'

    def fails(*args, **kwargs):
        raise OSError('synthetic volume failure')

    monkeypatch.setattr(zipfile.ZipFile, 'open', fails)
    with pytest.raises(OSError):
        module.package_directory(source, output)
    assert not output.exists()
    assert original.read_bytes() == b'old artifact'
    assert (source / 'nested/config.json').read_bytes() == b'synthetic bytes'


def test_archive_cannot_contaminate_media(tmp_path):
    source, _ = roots(tmp_path)
    with pytest.raises(ValueError):
        module.package_directory(source, source / 'archive.zip')
    assert not (source / 'archive.zip').exists()


def test_empty_or_failed_scan_never_publishes(tmp_path, monkeypatch):
    source, destination = roots(tmp_path)
    output = destination / 'model.zip'

    def cannot_scan(path, *, followlinks, onerror):
        onerror(PermissionError('synthetic unreadable directory'))
        yield

    monkeypatch.setattr(module.os, 'walk', cannot_scan)
    with pytest.raises(PermissionError):
        module.package_directory(source, output)
    assert not list(destination.iterdir())
    monkeypatch.setattr(module.os, 'walk', lambda *args, **kwargs: iter([]))
    with pytest.raises(ValueError):
        module.package_directory(source, output)
    assert not list(destination.iterdir())


def test_publication_race_preserves_winner(tmp_path, monkeypatch):
    source, destination = roots(tmp_path)
    output = destination / 'model.zip'

    def another_writer(temporary, target, **kwargs):
        target.write_bytes(b'winner')
        raise FileExistsError('another artifact won')

    monkeypatch.setattr(module.os, 'link', another_writer)
    with pytest.raises(FileExistsError):
        module.package_directory(source, output)
    assert output.read_bytes() == b'winner'
    assert list(destination.iterdir()) == [output]


def test_main_prechecks_before_staging(tmp_path, monkeypatch):
    import sys
    source, destination = roots(tmp_path)
    destination.chmod(0o755)
    monkeypatch.setattr(sys, 'argv', ['package', '--cli', '/unused', '--source', str(source),
                                    '--staging-parent', str(source), '--output', str(destination / 'model.zip')])
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('staging must not run'))
    with pytest.raises(ValueError):
        module.main()


def test_main_reports_cli_failure(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    source, destination = roots(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['package', '--cli', '/unused', '--source', str(source),
                                    '--staging-parent', str(source), '--output', str(destination / 'model.zip')])
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr='[integrity] rejected\n'))
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1
    assert '[integrity]' in capsys.readouterr().err
    assert not list(destination.iterdir())
