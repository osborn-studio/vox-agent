"""Tests for engine.plugins — plugin discovery and loading."""

import json
import pytest
from engine.plugins import PluginLoader


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


def make_plugin(plugins_dir, name, manifest_data):
    d = plugins_dir / name
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps(manifest_data))
    return d


class TestPluginScan:
    def test_empty(self, plugins_dir):
        loader = PluginLoader(plugins_dir)
        assert loader.scan() == []

    def test_scan_single(self, plugins_dir):
        make_plugin(plugins_dir, "test-plugin", {
            "name": "test-plugin", "version": "1.0.0",
            "description": "A test plugin",
        })
        loader = PluginLoader(plugins_dir)
        manifests = loader.scan()
        assert len(manifests) == 1
        assert manifests[0].name == "test-plugin"

    def test_scan_multiple(self, plugins_dir):
        make_plugin(plugins_dir, "plugin-a", {"name": "plugin-a"})
        make_plugin(plugins_dir, "plugin-b", {"name": "plugin-b"})
        loader = PluginLoader(plugins_dir)
        assert len(loader.scan()) == 2

    def test_skip_invalid(self, plugins_dir):
        make_plugin(plugins_dir, "good", {"name": "good"})
        bad_dir = plugins_dir / "bad"
        bad_dir.mkdir()
        (bad_dir / "plugin.json").write_text("invalid json{{{")
        loader = PluginLoader(plugins_dir)
        assert len(loader.scan()) == 1


class TestPluginLoad:
    def test_load(self, plugins_dir):
        make_plugin(plugins_dir, "my-plugin", {
            "name": "my-plugin", "version": "2.0.0",
            "tools": [{"name": "custom-tool"}],
            "skills": ["review.md"],
        })
        # Create the skill file
        (plugins_dir / "my-plugin" / "review.md").write_text("# Review skill")

        loader = PluginLoader(plugins_dir)
        result = loader.load("my-plugin")
        assert result is not None
        assert result.manifest.version == "2.0.0"
        assert result.tools_loaded == 1
        assert result.skills_loaded == 1

    def test_load_nonexistent(self, plugins_dir):
        loader = PluginLoader(plugins_dir)
        assert loader.load("nope") is None

    def test_load_all(self, plugins_dir):
        make_plugin(plugins_dir, "a", {"name": "a"})
        make_plugin(plugins_dir, "b", {"name": "b"})
        loader = PluginLoader(plugins_dir)
        loaded = loader.load_all()
        assert len(loaded) == 2

    def test_disabled_plugin(self, plugins_dir):
        make_plugin(plugins_dir, "off", {"name": "off", "enabled": False})
        loader = PluginLoader(plugins_dir)
        loaded = loader.load_all()
        assert len(loaded) == 0

    def test_unload(self, plugins_dir):
        make_plugin(plugins_dir, "temp", {"name": "temp"})
        loader = PluginLoader(plugins_dir)
        loader.load("temp")
        assert loader.unload("temp")
        assert len(loader.get_loaded()) == 0

    def test_missing_skill_error(self, plugins_dir):
        make_plugin(plugins_dir, "broken", {
            "name": "broken", "skills": ["nonexistent.md"],
        })
        loader = PluginLoader(plugins_dir)
        result = loader.load("broken")
        assert len(result.errors) == 1

    def test_get_all_skill_paths(self, plugins_dir):
        d = make_plugin(plugins_dir, "with-skills", {
            "name": "with-skills", "skills": ["s1.md", "s2.md"],
        })
        (d / "s1.md").write_text("skill 1")
        (d / "s2.md").write_text("skill 2")

        loader = PluginLoader(plugins_dir)
        loader.load("with-skills")
        paths = loader.get_all_skill_paths()
        assert len(paths) == 2

    def test_hooks_counted(self, plugins_dir):
        make_plugin(plugins_dir, "hooked", {
            "name": "hooked",
            "hooks": {"PreToolUse": ["check.py"], "PostToolUse": ["log.py", "audit.py"]},
        })
        loader = PluginLoader(plugins_dir)
        result = loader.load("hooked")
        assert result.hooks_loaded == 3
