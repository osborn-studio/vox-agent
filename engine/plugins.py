"""Plugin System — extend engine with external plugins.

Plugins can add:
- Custom tools
- Custom skills
- Custom hooks (pre/post tool use)
- Custom voice commands

Plugin format (plugin.json):
{
    "name": "my-plugin",
    "version": "1.0.0",
    "tools": [...],
    "skills": ["skill1.md", "skill2.md"],
    "hooks": {"PreToolUse": ["check.py"], "PostToolUse": ["log.py"]}
}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_PLUGINS_DIR = resolve_vox_agent_data_dir() / "plugins"


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    tools: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    hooks: dict[str, list[str]] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    path: Path
    tools_loaded: int = 0
    skills_loaded: int = 0
    hooks_loaded: int = 0
    errors: list[str] = field(default_factory=list)


class PluginLoader:
    """Discover and load plugins from the plugins directory."""

    def __init__(self, plugins_dir: Path | None = None) -> None:
        self.plugins_dir = plugins_dir or (resolve_vox_agent_data_dir() / "plugins")
        self._loaded: dict[str, LoadedPlugin] = {}

    def scan(self) -> list[PluginManifest]:
        """Scan for available plugins (without loading them)."""
        if not self.plugins_dir.exists():
            return []

        manifests = []
        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "plugin.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text())
                manifests.append(PluginManifest(**data))
            except Exception:
                logger.exception("failed to parse %s", manifest_path)

        return manifests

    def load_all(self) -> list[LoadedPlugin]:
        """Load all enabled plugins."""
        results = []
        for manifest in self.scan():
            if manifest.enabled:
                result = self.load(manifest.name)
                if result:
                    results.append(result)
        logger.info("loaded %d plugins", len(results))
        return results

    def load(self, plugin_name: str) -> LoadedPlugin | None:
        """Load a specific plugin by name."""
        plugin_dir = self.plugins_dir / plugin_name
        manifest_path = plugin_dir / "plugin.json"

        if not manifest_path.exists():
            logger.warning("plugin not found: %s", plugin_name)
            return None

        try:
            data = json.loads(manifest_path.read_text())
            manifest = PluginManifest(**data)
        except Exception:
            logger.exception("failed to parse plugin %s", plugin_name)
            return None

        loaded = LoadedPlugin(manifest=manifest, path=plugin_dir)

        # Load skills
        for skill_file in manifest.skills:
            skill_path = plugin_dir / skill_file
            if skill_path.exists():
                loaded.skills_loaded += 1
            else:
                loaded.errors.append(f"skill not found: {skill_file}")

        # Count tools
        loaded.tools_loaded = len(manifest.tools)

        # Count hooks
        for hook_type, hook_files in manifest.hooks.items():
            loaded.hooks_loaded += len(hook_files)

        self._loaded[plugin_name] = loaded
        logger.info(
            "plugin loaded: %s v%s (%d tools, %d skills, %d hooks)",
            manifest.name, manifest.version,
            loaded.tools_loaded, loaded.skills_loaded, loaded.hooks_loaded,
        )
        return loaded

    def unload(self, plugin_name: str) -> bool:
        return self._loaded.pop(plugin_name, None) is not None

    def get_loaded(self) -> list[LoadedPlugin]:
        return list(self._loaded.values())

    def get_plugin_skills_dir(self, plugin_name: str) -> Path | None:
        """Get the skills directory for a plugin."""
        loaded = self._loaded.get(plugin_name)
        if not loaded:
            return None
        return loaded.path

    def get_all_skill_paths(self) -> list[Path]:
        """Get all skill file paths from all loaded plugins."""
        paths = []
        for loaded in self._loaded.values():
            for skill_file in loaded.manifest.skills:
                path = loaded.path / skill_file
                if path.exists():
                    paths.append(path)
        return paths
