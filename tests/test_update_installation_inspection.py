import importlib.util
from pathlib import Path
import plistlib

import pytest

spec = importlib.util.spec_from_file_location(
    "inspect_install", Path(__file__).parents[1] / "scripts/inspect_update_installation.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_reads_only_bundle_metadata_and_does_not_execute_helper(tmp_path):
    app = tmp_path / "Vox Agent.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleShortVersionString": "4.2.3",
                "CFBundleVersion": "9",
                "CFBundleIdentifier": "test.vox",
            }
        )
    )
    helpers = contents / "MacOS"
    helpers.mkdir()
    executable = helpers / "qwen3-asr-server"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o755)
    before = executable.read_bytes()
    result = module.inspect_app(app)
    assert result["version"] == "4.2.3"
    assert result["helper_layouts"] == ["onefile"]
    assert result["bundled_qwen_model_present"] is False
    assert result["trust_verified"] is False
    assert executable.read_bytes() == before


def test_symlink_bundle_refused(tmp_path):
    link = tmp_path / "linked.app"
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError):
        module.inspect_app(link)


def test_symlinked_contents_rejected(tmp_path):
    app = tmp_path / "Vox Agent.app"
    app.mkdir()
    (app / "Contents").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="Symlink"):
        module.inspect_app(app)
