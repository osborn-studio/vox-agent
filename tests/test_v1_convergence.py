"""Structural guardrails for the completed V1/core retirement."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_package_is_retired_and_has_no_live_imports():
    assert not (ROOT / "core").exists()

    live_roots = [ROOT / "runtime", ROOT / "engine", ROOT / "apps"]
    python_files = [ROOT / "export_cron.py"]
    for live_root in live_roots:
        python_files.extend(live_root.rglob("*.py"))

    offenders: list[str] = []
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "core":
                offenders.append(str(path.relative_to(ROOT)))
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "core" for alias in node.names
            ):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_vox_engine_is_the_only_installed_python_command():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {"vox-engine": "engine.cli:main"}


def test_wheel_does_not_package_core():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "core" not in packages
