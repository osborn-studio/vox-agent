"""Runtime data-retention policy (enterprise admin, master brief §9.2 / §11.6).

Holds optional runtime overrides for how long audio, transcripts, and memory
nodes are kept, plus whether expired data is auto-deleted. Overrides are layered
over the VOXAGENT_AUDIO_RETENTION_DAYS / VOXAGENT_TRANSCRIPT_RETENTION_DAYS /
VOXAGENT_MEMORY_RETENTION_DAYS / VOXAGENT_AUTO_DELETE env defaults. `effective()`
reports the active retention window per data class alongside which values are
runtime overrides vs. env defaults.

The application context owns the active instance and persists admin overrides
through ``PolicyStore``. ``RetentionSweeper`` consumes the effective values for
previewed, manual, and scheduled deletion runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Retention windows in days; the memory window is longer than audio/transcript
# because distilled memory nodes are the durable product, raw media is not.
_DEFAULT_AUDIO_DAYS = 30
_DEFAULT_TRANSCRIPT_DAYS = 90
_DEFAULT_MEMORY_DAYS = 365

_TRUE = {"1", "true", "yes", "on"}


def _env_days(var: str, fallback: int) -> int:
    raw = os.environ.get(var)
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return value if value >= 1 else fallback


def _env_auto_delete() -> bool:
    return os.environ.get("VOXAGENT_AUTO_DELETE", "").strip().lower() in _TRUE


@dataclass
class RetentionPolicy:
    # Runtime overrides; None means "fall back to the env default".
    audio_retention_days: int | None = None
    transcript_retention_days: int | None = None
    memory_retention_days: int | None = None
    auto_delete_enabled: bool | None = None

    def effective(self) -> dict:
        return {
            "audioRetentionDays": (
                self.audio_retention_days
                if self.audio_retention_days is not None
                else _env_days("VOXAGENT_AUDIO_RETENTION_DAYS", _DEFAULT_AUDIO_DAYS)
            ),
            "transcriptRetentionDays": (
                self.transcript_retention_days
                if self.transcript_retention_days is not None
                else _env_days("VOXAGENT_TRANSCRIPT_RETENTION_DAYS", _DEFAULT_TRANSCRIPT_DAYS)
            ),
            "memoryRetentionDays": (
                self.memory_retention_days
                if self.memory_retention_days is not None
                else _env_days("VOXAGENT_MEMORY_RETENTION_DAYS", _DEFAULT_MEMORY_DAYS)
            ),
            "autoDeleteEnabled": (
                self.auto_delete_enabled
                if self.auto_delete_enabled is not None
                else _env_auto_delete()
            ),
            "audioRetentionDaysOverride": self.audio_retention_days,
            "transcriptRetentionDaysOverride": self.transcript_retention_days,
            "memoryRetentionDaysOverride": self.memory_retention_days,
            "autoDeleteOverride": self.auto_delete_enabled,
        }
