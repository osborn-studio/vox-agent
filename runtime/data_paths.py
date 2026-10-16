"""Canonical filesystem locations owned by Vox Agent."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def resolve_vox_agent_data_dir(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    platform_name: str | None = None,
) -> Path:
    """Resolve the product-owned data directory without touching the filesystem."""

    values = os.environ if environ is None else environ
    override = values.get("VOXAGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    resolved_home = home or Path.home()
    platform = platform_name or sys.platform
    if platform.startswith("win"):
        local_app_data = values.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data).expanduser() if local_app_data else (
            resolved_home / "AppData" / "Local"
        )
        return base / "Vox Agent"
    return resolved_home / ".voxagent"


def legacy_voxlog_data_dirs(*, home: Path | None = None) -> tuple[Path, ...]:
    """Return allowlisted read-only compatibility sources, newest first."""

    resolved_home = home or Path.home()
    return (
        resolved_home / ".voxlog2",
        resolved_home / ".voxlog",
        resolved_home / "Library" / "Application Support" / "VoxLog",
    )
