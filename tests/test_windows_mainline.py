"""Structural checks for the single Windows-capable Tauri mainline."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAURI_ROOT = ROOT / "apps" / "desktop-tauri"


def test_desktop_tauri_is_the_only_active_shell():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "apps/desktop-tauri" in package["workspaces"]
    assert not (ROOT / "app" / "frontend").exists()
    assert "/Users/" not in json.dumps(package)
    assert "dev-token" not in json.dumps(package)


def test_windows_installer_and_ci_are_enabled():
    config = json.loads(
        (TAURI_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    assert config["bundle"]["active"] is True
    assert config["bundle"]["targets"] == "all"

    workflow = (ROOT / ".github" / "source-workflows" / "test-engine.yml.txt").read_text(
        encoding="utf-8"
    )
    assert "windows-tauri-test:" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "legacy-windows-ui-test:" not in workflow


def test_canonical_shell_has_no_fixed_token_or_developer_path():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TAURI_ROOT.rglob("*")
        if path.is_file()
        and "target" not in path.parts
        and "node_modules" not in path.parts
        and path.suffix in {".rs", ".js", ".html", ".json", ".md"}
    )
    assert "voxlog-dev-token" not in source
    assert "dev-token" not in source
    assert "/Users/" not in source
    assert "Documents/CODE" not in source


def test_windows_docs_point_to_canonical_shell():
    for relative_path in (
        "docs/windows-port-osstack-plan.md",
        "docs/windows-port-feature-parity.md",
    ):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "apps/desktop-tauri" in contents
        assert "`app/frontend`" not in contents
