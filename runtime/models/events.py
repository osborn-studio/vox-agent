"""Voice event data model and event contracts for VoxLog v2.

These types are the Python-side implementation of the Phase 0 contract:
  recording -> STT -> fastpath result -> output -> slowpath archive
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class RecordingMode(str, Enum):
    NORMAL = "normal"       # Archive + searchable + exportable
    PRIVATE = "private"     # Archive but hidden, no auto-export
    EPHEMERAL = "ephemeral" # No archive, instant output only


class SessionType(str, Enum):
    CODING = "coding"
    PLANNING = "planning"
    REVIEW = "review"
    JOURNAL = "journal"
    CHAT_WITH_AI = "chat_with_ai"
    GENERAL = "general"


class TargetRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OutputMode(str, Enum):
    PASTE = "paste"
    DIRECT_TYPING = "direct_typing"
    PREVIEW_ONLY = "preview_only"
    NONE = "none"


class FastPathStatus(str, Enum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ArchiveStatus(str, Enum):
    SKIPPED = "skipped"         # Event intentionally not archived
    QUEUED = "queued"           # Fast path done, slow path not started
    RAW_ONLY = "raw_only"       # Archived without polish
    POLISHED = "polished"       # LLM polish complete
    FAILED = "failed"           # Slow path failed


class ExportStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIP = "skip"              # Private/ephemeral mode


class VoiceEvent(BaseModel):
    """Core data model for every voice input."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    utterance_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    output_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Session
    session_id: str = ""
    session_type: SessionType = SessionType.GENERAL

    # Source
    source: str = "desktop"  # desktop | feishu | wecom | web
    env: str = "home"        # home | office | mobile
    agent: str = ""          # which agent this is addressed to

    # Target
    target_app: str = ""     # frontmost app name
    target_risk_level: TargetRiskLevel = TargetRiskLevel.LOW

    # Content — three versions
    raw_text: str = ""           # exact ASR output
    display_text: str = ""       # fast path: dictionary-corrected, used for paste
    polished_text: str = ""      # slow path: LLM enhanced (filled async)

    # Audio metadata
    audio_duration_ms: int = 0
    audio_file: str = ""

    # Provider info
    stt_provider: str = ""
    stt_model: str = ""
    llm_provider: str = ""

    # Latency
    latency_stt_ms: int = 0
    latency_total_fast_ms: int = 0
    latency_total_slow_ms: int = 0

    # Classification
    language_mix: str = "mixed"  # zh | en | mixed
    tags: list[str] = Field(default_factory=list)

    # Privacy
    recording_mode: RecordingMode = RecordingMode.NORMAL

    # Output
    output_mode: OutputMode = OutputMode.PASTE
    confidence: float = 0.0

    # Status
    archive_status: ArchiveStatus = ArchiveStatus.QUEUED
    export_status: ExportStatus = ExportStatus.PENDING

    # Role in conversation
    role: str = "me"  # me | other


class FastPathResult(BaseModel):
    """What the fast path returns to the UI immediately."""
    id: str
    status: FastPathStatus = FastPathStatus.OK
    display_text: str
    raw_text: str
    stt_provider: str
    stt_model: str = ""
    target_app: str = ""
    target_risk_level: TargetRiskLevel = TargetRiskLevel.LOW
    should_autopaste: bool = True
    needs_review: bool = False
    confidence: float = 0.0
    dictionary_applied: list[dict[str, str]] = Field(default_factory=list)
    latency_ms: int


class SlowPathUpdate(BaseModel):
    """What the slow path produces asynchronously."""
    id: str
    polished_text: str = ""
    llm_provider: str = ""
    tags: list[str] = Field(default_factory=list)
    archive_status: ArchiveStatus = ArchiveStatus.RAW_ONLY
    latency_slow_ms: int = 0


class EventEnvelope(BaseModel):
    """Cross-module event wrapper for desktop/runtime contracts."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str = ""
    utterance_id: str = ""
    source: str = "runtime"
    version: int = 1
    payload: dict = Field(default_factory=dict)
