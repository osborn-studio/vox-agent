"""Release packages must include the runtime pieces exercised only by a real server."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_websocket_server_runtime_is_a_production_dependency_and_bundled():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    bundle_script = (ROOT / "scripts/build_voxagent_dmg.sh").read_text(encoding="utf-8")

    assert any(dependency.startswith("websockets") for dependency in dependencies)
    assert "--collect-all websockets" in bundle_script
