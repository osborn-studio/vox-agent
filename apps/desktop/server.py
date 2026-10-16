"""Vox Agent desktop compatibility server — Fast Path + Slow Path architecture.

Fast path: STT + correction → return immediately
Slow path: polish + archive + index (background)

Endpoints:
  POST /v1/voice       — fast path: audio → display_text (immediate)
  POST /v1/save        — save pasted text directly
  GET  /v1/history     — search/list by agent
  GET/POST /v1/migration/* — bounded portable history/dictionary transfer
  GET  /v1/agents      — list agents
  DELETE /v1/history/{id} — recall (2 min window)
  POST /v1/asr/switch  — switch ASR model
  GET  /v1/detect      — auto-detect network profile
  GET  /v1/dictionary  — get dictionary
  POST /v1/dictionary  — update dictionary
  GET  /v1/stats       — usage stats
  GET  /health         — health check
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import structlog
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Header, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse

from engine import __version__
from engine.legacy_migration import (
    discover_legacy_databases,
    inspect_legacy_database,
    read_legacy_database,
)
from integrations.ai_mate_memory import AIMateMemorySink
from integrations.obsidian import (
    DEFAULT_OBSIDIAN_VAULT,
    ObsidianSink,
    sync_recent,
    voice_log_dir,
)
from memory.sqlite_store import SQLiteStore
from runtime.data_paths import legacy_voxlog_data_dirs, resolve_vox_agent_data_dir
from runtime.fastpath.corrector import Corrector
from runtime.fastpath.pipeline import fast_path
from runtime.fastpath.recent import RecentUtteranceStore
from runtime.fastpath.stt import STTError
from runtime.llm_provider import resolve_llm_provider
from runtime.models.config import VoxAgentConfig, get_config
from runtime.provider_qualification import build_provider_qualification
from runtime.provider_settings import STT_ROUTES, apply_provider_settings_update
from runtime.models.events import (
    ArchiveStatus,
    ExportStatus,
    FastPathResult,
    FastPathStatus,
    OutputMode,
    RecordingMode,
    TargetRiskLevel,
    VoiceEvent,
)
from runtime.slowpath.polisher import Polisher
from runtime.slowpath.digester import DailyDigester, ProjectDigester, SessionDigester
from runtime.slowpath.enhancer import DigestEnhancer
from runtime.slowpath.worker import enqueue, start_worker

logger = structlog.get_logger()

# Singletons
_config: VoxAgentConfig | None = None
_store: SQLiteStore | None = None
_corrector: Corrector | None = None
_polisher: Polisher | None = None
_stt_override: str | None = None
_recent: RecentUtteranceStore | None = None
_session_digester = SessionDigester()
_daily_digester = DailyDigester()
_project_digester = ProjectDigester()
_digest_enhancer = DigestEnhancer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _store, _corrector, _polisher, _recent
    _config = get_config()
    _config.log_dir.mkdir(parents=True, exist_ok=True)

    # Store
    _store = SQLiteStore(_config.db_path)
    await _store.init()

    # Corrector (load three dictionary layers)
    _corrector = Corrector()
    dict_dir = _config.terms_dir
    personal = _terms_path()
    _corrector.load(
        dict_dir / "base.json",
        dict_dir / "technical.json",
        personal if personal.exists() else dict_dir.parent / "terms.json",
    )

    # Polisher
    _polisher = Polisher()
    _recent = RecentUtteranceStore(ttl_seconds=30)

    # Auto-detect profile
    import os
    if not (os.getenv("VOXAGENT_PROFILE") or os.getenv("VOXLOG_ENV")):
        from runtime.routing.profiles import detect_profile
        await detect_profile(_config)

    # Start slow path worker
    await start_worker(_config, _store, _polisher)

    logger.info("server.started", port=_config.port, profile=_config.active_profile)
    yield

    await _store.close()
    logger.info("server.stopped")


app = FastAPI(title="Vox Agent Desktop Bridge", version=__version__, lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def verify_token(authorization: str | None = Header(None)) -> None:
    if not _config or not _config.api_token:
        return
    if not authorization:
        raise HTTPException(401, "Missing auth")
    if authorization.removeprefix("Bearer ").strip() != _config.api_token:
        raise HTTPException(401, "Invalid token")


def _terms_path() -> Path:
    assert _config
    canonical = _config.data_dir / "terms.json"
    if canonical.exists():
        return canonical
    legacy = legacy_voxlog_data_dirs()[0] / "terms.json"
    if legacy.exists():
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(legacy.read_bytes())
        return canonical
    return canonical


def _load_terms() -> dict:
    path = _terms_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "corrections": {}, "preserve": [], "format_rules": {"cn_en_space": True}}


def _settings_payload() -> dict:
    assert _config
    return {
        "providers": {
            "dashscope_us": {"configured": bool(_config.dashscope_key_us)},
            "dashscope_cn": {"configured": bool(_config.dashscope_key_cn)},
            "openai": {"configured": bool(_config.openai_key)},
            "siliconflow": {"configured": bool(_config.siliconflow_key)},
            "doubao": {"configured": resolve_llm_provider(_config, "doubao") is not None},
            "anthropic": {"configured": resolve_llm_provider(_config, "claude") is not None},
            "custom_llm": {
                "configured": resolve_llm_provider(_config, "openai-compatible-custom") is not None
            },
        },
        "active_profile": _config.active_profile,
        "digest_enhancement_enabled": bool(_config.digest_enhancement_enabled),
        "digest_enhancement_provider": _config.digest_enhancement_provider,
        "llm_config": {
            "openai_model": _config.openai_llm_model,
            "qwen_model": _config.qwen_llm_model,
            "doubao_model": _config.doubao_model,
            "anthropic_model": _config.anthropic_model,
            "custom_base_url": _config.custom_llm_base_url,
            "custom_model": _config.custom_llm_model,
            "ollama_model": _config.ollama_model,
        },
        "profiles": [
            {
                "name": profile.name,
                "stt_main": profile.stt_main,
                "stt_fallback": profile.stt_fallback,
                "llm_main": profile.llm_main,
                "llm_fallback": profile.llm_fallback,
            }
            for profile in _config.profiles.values()
        ],
        "backend_auth_required": bool(_config.api_token),
    }


def _fastpath_response_payload(result: FastPathResult, event: VoiceEvent, *, agent: str, role: str) -> dict:
    return {
        "id": result.id,
        "status": result.status.value,
        "raw_text": result.raw_text,
        "display_text": result.display_text,
        "polished_text": result.display_text,
        "stt_provider": result.stt_provider,
        "stt_model": result.stt_model,
        "target_risk_level": result.target_risk_level.value,
        "should_autopaste": result.should_autopaste,
        "needs_review": result.needs_review,
        "confidence": result.confidence,
        "dictionary_applied": result.dictionary_applied,
        "latency_ms": result.latency_ms,
        "target_app": event.target_app,
        "session_id": event.session_id,
        "utterance_id": event.utterance_id,
        "output_id": event.output_id,
        "output_mode": event.output_mode.value,
        "archive_status": event.archive_status.value,
        "agent": agent,
        "env": _config.active_profile if _config else "home",
        "role": role,
        "created_at": event.created_at.isoformat(),
    }


def _provider_connectivity_payload() -> dict:
    assert _config
    profile = _config.profiles[_config.active_profile]
    qualification = build_provider_qualification(_config)
    providers = qualification["providers"]
    configured_count = sum(1 for provider in providers if provider["configured"])

    checks: list[dict[str, str]] = [
        {
            "key": "backend",
            "label": "Backend Bridge",
            "status": "ok",
            "message": "The compatibility runtime is responding behind the gateway.",
        }
    ]

    if configured_count:
        checks.append(
            {
                "key": "providers",
                "label": "Provider Routes",
                "status": "ok",
                "message": f"{configured_count} provider route(s) configured.",
            }
        )
    else:
        checks.append(
            {
                "key": "providers",
                "label": "Provider Routes",
                "status": "fail",
                "message": "Configure at least one provider route to enable fallback.",
            }
        )

    preferred_provider = profile.stt_main
    preferred_entry = next(
        (provider for provider in providers if "stt_main" in provider["active_roles"]),
        None,
    )
    preferred_ready = bool(preferred_entry and preferred_entry["configured"])

    checks.append(
        {
            "key": "stt-profile",
            "label": "Active STT Profile",
            "status": "ok" if preferred_ready else "warn",
            "message": (
                f"Active profile '{_config.active_profile}' has {preferred_provider} configured; run a live smoke for evidence."
                if preferred_ready
                else f"Active profile '{_config.active_profile}' prefers {preferred_provider}, but its credential, runtime, or assets are missing."
            ),
        }
    )

    return {
        "ready": qualification["summary"]["active_routes_configured"],
        "active_profile": _config.active_profile,
        "recommended_stt_provider": preferred_provider,
        "backend_url_reachable": True,
        "configured_provider_count": configured_count,
        "checks": checks,
    }


async def _export_digest_markdown(scope: str, *, session_id: str = "", digest_date: str = "", project_key: str = "") -> str:
    digest = await _rebuild_digest(
        scope,
        session_id=session_id,
        digest_date=digest_date,
        project_key=project_key,
    )
    header = f"# {digest['digest_type']}"
    meta = []
    if digest.get("session_id"):
        meta.append(f"session_id: {digest['session_id']}")
    if digest.get("digest_date"):
        meta.append(f"date: {digest['digest_date']}")
    if digest.get("project_key"):
        meta.append(f"project: {digest['project_key']}")
    meta.append(f"enhancer: {digest.get('enhancer_provider', 'heuristic')}")
    tags = ", ".join(digest.get("suggested_tags", [])) or "(none)"
    entities = ", ".join(digest.get("mentioned_entities", [])) or "(none)"
    lines = [
        header,
        "",
        *meta,
        "",
        "## Summary",
        digest.get("summary", ""),
        "",
        "## Intent",
        digest.get("intent", ""),
        "",
        "## Suggested Tags",
        tags,
        "",
        "## Mentioned Entities",
        entities,
        "",
    ]
    return "\n".join(lines)


async def _rebuild_digest(scope: str, *, session_id: str = "", digest_date: str = "", project_key: str = "") -> dict:
    assert _store and _config
    if scope == "session":
        if not session_id:
            raise HTTPException(400, "session_id required")
        events = await _store.list_events_for_session(session_id)
        if not events:
            raise HTTPException(404, "No session events found")
        model = events[-1]
        digest = _session_digester.build(model)
        source_text = model.polished_text or model.display_text or model.raw_text
        digest = await _digest_enhancer.enhance(digest, source_text, _config)
        await _store.upsert_session_digest(
            session_id=digest["session_id"],
            source_event_id=digest["source_event_id"],
            summary=digest["summary"],
            intent=digest["intent"],
            suggested_tags=digest["suggested_tags"],
            mentioned_entities=digest["mentioned_entities"],
            enhanced=digest.get("enhanced", False),
            enhancer_provider=digest.get("enhancer_provider", "heuristic"),
        )
        return await _store.get_session_digest(session_id)
    if scope == "daily":
        if not digest_date:
            raise HTTPException(400, "date required")
        events = await _store.list_events_for_date(digest_date)
        if not events:
            raise HTTPException(404, "No daily events found")
        digest = _daily_digester.build(events, date_key=digest_date)
        source_text = "\n".join(
            (item.polished_text or item.display_text or item.raw_text).strip()
            for item in events
            if (item.polished_text or item.display_text or item.raw_text).strip()
        )
        digest = await _digest_enhancer.enhance(digest, source_text, _config)
        await _store.upsert_daily_digest(
            digest_date=digest["digest_date"],
            source_event_id=digest["source_event_id"],
            summary=digest["summary"],
            intent=digest["intent"],
            suggested_tags=digest["suggested_tags"],
            mentioned_entities=digest["mentioned_entities"],
            enhanced=digest.get("enhanced", False),
            enhancer_provider=digest.get("enhancer_provider", "heuristic"),
        )
        return await _store.get_daily_digest(digest_date)
    if scope == "project":
        project_value = project_key.strip().lower()
        if not project_value:
            raise HTTPException(400, "project_key required")
        events = await _store.list_events_for_project(project_value)
        if not events:
            raise HTTPException(404, "No project events found")
        digest = _project_digester.build(events, project_key=project_value)
        source_text = "\n".join(
            (item.polished_text or item.display_text or item.raw_text).strip()
            for item in events
            if (item.polished_text or item.display_text or item.raw_text).strip()
        )
        digest = await _digest_enhancer.enhance(digest, source_text, _config)
        await _store.upsert_project_digest(
            project_key=digest["project_key"],
            source_event_id=digest["source_event_id"],
            summary=digest["summary"],
            intent=digest["intent"],
            suggested_tags=digest["suggested_tags"],
            mentioned_entities=digest["mentioned_entities"],
            enhanced=digest.get("enhanced", False),
            enhancer_provider=digest.get("enhancer_provider", "heuristic"),
        )
        return await _store.get_project_digest(project_value)
    raise HTTPException(400, "Unknown digest scope")


def _write_terms(data: dict) -> dict:
    path = _terms_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if _corrector:
        _corrector.corrections = data.get("corrections", {})
        _corrector.preserve = data.get("preserve", [])
        _corrector.cn_en_space = data.get("format_rules", {}).get("cn_en_space", True)
    return data


def _recent_payload(item) -> dict:
    return {
        "id": item.event.id,
        "utterance_id": item.event.utterance_id,
        "session_id": item.event.session_id,
        "output_id": item.event.output_id,
        "status": item.result.status.value,
        "raw_text": item.result.raw_text,
        "display_text": item.result.display_text,
        "target_app": item.event.target_app,
        "target_risk_level": item.event.target_risk_level.value,
        "recording_mode": item.event.recording_mode.value,
        "output_mode": item.event.output_mode.value,
        "archive_status": item.event.archive_status.value,
        "confidence": item.result.confidence,
        "stt_provider": item.result.stt_provider,
        "stt_model": item.result.stt_model,
        "dictionary_applied": item.result.dictionary_applied,
        "expires_at": int(item.expires_at * 1000),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "vox-agent-desktop",
        "version": __version__,
        "profile": _config.active_profile if _config else "unknown",
    }


# === FAST PATH ===

@app.post("/v1/voice")
async def voice_endpoint(
    audio: UploadFile = File(...),
    source: str = Form("desktop"),
    env: str = Form("auto"),
    agent: str = Form(""),
    target_app: str = Form(""),
    session_id: str = Form(""),
    mode: str = Form("normal"),
    _auth: None = Depends(verify_token),
):
    assert _config and _corrector and _store

    audio_bytes = await audio.read()
    if len(audio_bytes) < 100:
        raise HTTPException(422, "Audio too short")

    # Validate WAV if applicable
    from runtime.audio import detect_format, validate_wav
    fmt = detect_format(audio_bytes)
    if fmt == "wav":
        valid, err = validate_wav(audio_bytes, max_seconds=_config.max_audio_seconds)
        if not valid and "too long" in err:
            raise HTTPException(413, err)

    recording_mode = RecordingMode(mode) if mode in ("normal", "private", "ephemeral") else RecordingMode.NORMAL

    try:
        result, event = await fast_path(
            audio_bytes, _config, _corrector,
            source=source, agent=agent, target_app=target_app,
            session_id=session_id,
            recording_mode=recording_mode, stt_override=_stt_override,
        )
    except STTError as e:
        raise HTTPException(502, str(e))

    # Enqueue for slow path (non-blocking)
    enqueue(event)
    _recent.put(result, event, audio_bytes)

    return _fastpath_response_payload(result, event, agent=agent, role="me")


@app.post("/v1/voice/text")
async def text_voice_endpoint(request: Request, _auth: None = Depends(verify_token)):
    assert _config and _corrector and _store and _recent
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(422, "text required")

    source = str(body.get("source", "gateway-text")).strip() or "gateway-text"
    agent = str(body.get("agent", "")).strip()
    target_app = str(body.get("target_app", "")).strip() or source
    session_id = str(body.get("session_id", "")).strip()
    role = str(body.get("role", "other")).strip() or "other"
    mode = str(body.get("mode", "normal")).strip().lower()
    recording_mode = RecordingMode(mode) if mode in ("normal", "private", "ephemeral") else RecordingMode.NORMAL

    started_at = time.monotonic()
    display_text, dictionary_applied = _corrector.correct_with_trace(text)
    latency_ms = int((time.monotonic() - started_at) * 1000)
    confidence = 0.98 if text else 0.0
    risk = TargetRiskLevel.HIGH if "terminal" in target_app.lower() else TargetRiskLevel.LOW
    should_autopaste = risk != TargetRiskLevel.HIGH
    result = FastPathResult(
        id="",
        status=FastPathStatus.OK,
        display_text=display_text,
        raw_text=text,
        stt_provider="gateway-text",
        stt_model="text-v1",
        target_app=target_app,
        target_risk_level=risk,
        should_autopaste=should_autopaste,
        needs_review=False,
        confidence=confidence,
        dictionary_applied=dictionary_applied,
        latency_ms=latency_ms,
    )
    event = VoiceEvent(
        source=source,
        env=_config.active_profile,
        agent=agent,
        target_app=target_app,
        target_risk_level=risk,
        raw_text=text,
        display_text=display_text,
        polished_text="",
        audio_duration_ms=0,
        stt_provider="gateway-text",
        stt_model="text-v1",
        latency_stt_ms=0,
        latency_total_fast_ms=latency_ms,
        recording_mode=recording_mode,
        output_mode=OutputMode.PASTE if should_autopaste else OutputMode.PREVIEW_ONLY,
        confidence=confidence,
        archive_status=ArchiveStatus.QUEUED,
        export_status=ExportStatus.PENDING if recording_mode == RecordingMode.NORMAL else ExportStatus.SKIP,
        session_id=session_id,
        role=role,
    )
    result.id = event.id
    enqueue(event)
    _recent.put(result, event, b"")
    return _fastpath_response_payload(result, event, agent=agent, role=role)


# === SAVE TEXT ===

@app.post("/v1/save")
async def save_endpoint(
    text: str = Form(...),
    source: str = Form("paste"),
    agent: str = Form(""),
    target_app: str = Form(""),
    session_id: str = Form(""),
    mode: str = Form("normal"),
    _auth: None = Depends(verify_token),
):
    assert _config and _store
    recording_mode = RecordingMode(mode) if mode in ("normal", "private", "ephemeral") else RecordingMode.NORMAL
    event = VoiceEvent(
        source=source, env=_config.active_profile, agent=agent,
        target_app=target_app or source,
        raw_text=text, display_text=text, polished_text=text,
        session_id=session_id,
        recording_mode=recording_mode,
        archive_status=ArchiveStatus.RAW_ONLY,
        export_status=ExportStatus.PENDING if recording_mode == RecordingMode.NORMAL else ExportStatus.SKIP,
        role="other",
    )
    enqueue(event)
    return {
        "id": event.id, "raw_text": text, "display_text": text,
        "polished_text": text, "latency_ms": 0, "role": "other",
        "session_id": event.session_id,
        "utterance_id": event.utterance_id,
        "output_id": event.output_id,
        "output_mode": event.output_mode.value,
        "archive_status": event.archive_status.value,
        "created_at": event.created_at.isoformat(),
    }


# === HISTORY ===

@app.get("/v1/agents")
async def agents_endpoint(_auth: None = Depends(verify_token)):
    assert _store
    return await _store.list_agents()


@app.get("/v1/history/agent")
async def history_by_agent(agent: str = Query(...), limit: int = Query(200), _auth: None = Depends(verify_token)):
    assert _store
    return await _store.list_by_agent(agent, limit)


@app.get("/v1/history")
async def history_endpoint(
    q: str = Query(""), date: str = Query(""), limit: int = Query(50),
    _auth: None = Depends(verify_token),
):
    assert _store
    if q:
        return await _store.search(q, limit)
    return []


@app.get("/v1/migration/history")
async def migration_history_endpoint(
    offset: int = Query(0, ge=0, le=50_000),
    limit: int = Query(500, ge=1, le=500),
    _auth: None = Depends(verify_token),
):
    assert _store
    try:
        return await _store.list_portable_history(offset=offset, limit=limit)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/v1/migration/history/import")
async def migration_history_import_endpoint(
    request: Request,
    _auth: None = Depends(verify_token),
):
    assert _store
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {"items"}:
        raise HTTPException(400, "migration history body must contain only items")
    items = body["items"]
    if not isinstance(items, list) or len(items) > 200:
        raise HTTPException(400, "migration history import accepts at most 200 items")
    try:
        return await _store.import_portable_history(items)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/v1/migration/dictionary/import")
async def migration_dictionary_import_endpoint(
    request: Request,
    _auth: None = Depends(verify_token),
):
    body = await request.json()
    if not isinstance(body, dict) or not set(body).issubset(
        {"version", "corrections", "preserve", "format_rules"}
    ):
        raise HTTPException(400, "migration dictionary contains unknown fields")
    corrections = body.get("corrections", {})
    preserve = body.get("preserve", [])
    format_rules = body.get("format_rules")
    version = body.get("version")
    if (
        (version is not None and (isinstance(version, bool) or not isinstance(version, int)))
        or not isinstance(corrections, dict)
        or len(corrections) > 10_000
        or not isinstance(preserve, list)
        or len(preserve) > 10_000
        or (format_rules is not None and not isinstance(format_rules, dict))
    ):
        raise HTTPException(400, "migration dictionary has invalid fields")
    if any(
        not isinstance(wrong, str)
        or not wrong.strip()
        or len(wrong) > 200
        or not isinstance(right, str)
        or not right.strip()
        or len(right) > 500
        for wrong, right in corrections.items()
    ) or any(
        not isinstance(term, str) or not term.strip() or len(term) > 500
        for term in preserve
    ):
        raise HTTPException(400, "migration dictionary contains invalid terms")
    if format_rules is not None and (
        not set(format_rules).issubset({"cn_en_space"})
        or (
            "cn_en_space" in format_rules
            and not isinstance(format_rules["cn_en_space"], bool)
        )
    ):
        raise HTTPException(400, "migration dictionary contains invalid format rules")

    current = _load_terms()
    current_corrections = current.setdefault("corrections", {})
    imported_corrections = 0
    skipped_corrections = 0
    for wrong, right in corrections.items():
        if wrong in current_corrections:
            skipped_corrections += 1
            continue
        current_corrections[wrong] = right
        imported_corrections += 1
    current_preserve = current.setdefault("preserve", [])
    existing_preserve = set(current_preserve)
    imported_preserved_terms = 0
    for term in preserve:
        if term in existing_preserve:
            continue
        current_preserve.append(term)
        existing_preserve.add(term)
        imported_preserved_terms += 1
    current.setdefault("version", version or 1)
    current.setdefault("format_rules", format_rules or {"cn_en_space": True})
    dictionary = _write_terms(current)
    return {
        "imported_corrections": imported_corrections,
        "skipped_corrections": skipped_corrections,
        "imported_preserved_terms": imported_preserved_terms,
        "dictionary": dictionary,
    }


def _legacy_database_paths() -> list[Path]:
    assert _config
    return discover_legacy_databases(home=Path.home(), canonical_db=_config.db_path)


@app.get("/v1/migration/legacy")
async def migration_legacy_inspect_endpoint(
    _auth: None = Depends(verify_token),
):
    sources = []
    for path in _legacy_database_paths():
        try:
            source = await asyncio.to_thread(inspect_legacy_database, path)
            sources.append(
                {
                    "path": str(path),
                    "kind": source.kind,
                    "records_found": source.records_found,
                    "imported": 0,
                    "skipped": 0,
                    "invalid": 0,
                    "error": None,
                }
            )
        except (ValueError, OSError, sqlite3.Error) as error:
            sources.append(
                {
                    "path": str(path),
                    "kind": "unsupported",
                    "records_found": 0,
                    "imported": 0,
                    "skipped": 0,
                    "invalid": 0,
                    "error": str(error),
                }
            )
    return {
        "sources": sources,
        "records_found": sum(source["records_found"] for source in sources),
        "imported": 0,
        "skipped": 0,
        "invalid": 0,
    }


@app.post("/v1/migration/legacy/import")
async def migration_legacy_import_endpoint(
    _auth: None = Depends(verify_token),
):
    assert _store
    sources = []
    total_found = 0
    total_imported = 0
    total_skipped = 0
    total_invalid = 0
    for path in _legacy_database_paths():
        try:
            history = await asyncio.to_thread(read_legacy_database, path)
            imported = 0
            skipped = 0
            for offset in range(0, len(history.records), 200):
                result = await _store.import_portable_history(history.records[offset : offset + 200])
                imported += result["imported"]
                skipped += result["skipped"]
            source_report = {
                "path": str(path),
                "kind": history.source.kind,
                "records_found": history.source.records_found,
                "imported": imported,
                "skipped": skipped,
                "invalid": history.invalid,
                "error": None,
            }
            total_found += history.source.records_found
            total_imported += imported
            total_skipped += skipped
            total_invalid += history.invalid
        except (ValueError, OSError, sqlite3.Error) as error:
            source_report = {
                "path": str(path),
                "kind": "unsupported",
                "records_found": 0,
                "imported": 0,
                "skipped": 0,
                "invalid": 0,
                "error": str(error),
            }
        sources.append(source_report)
    return {
        "sources": sources,
        "records_found": total_found,
        "imported": total_imported,
        "skipped": total_skipped,
        "invalid": total_invalid,
    }


@app.delete("/v1/history/{record_id}")
async def delete_record(record_id: str, _auth: None = Depends(verify_token)):
    assert _store
    ok = await _store.delete_event(record_id)
    if not ok:
        raise HTTPException(403, "Cannot recall (>2 min or not found)")
    return {"deleted": record_id}


@app.get("/v1/history/export", response_class=PlainTextResponse)
async def export_endpoint(date: str = Query(...), _auth: None = Depends(verify_token)):
    assert _store
    return await _store.export_markdown(date)


@app.get("/v1/digests/session")
async def session_digest_endpoint(
    session_id: str = Query(...),
    _auth: None = Depends(verify_token),
):
    assert _store
    digest = await _store.get_session_digest(session_id)
    if not digest:
        raise HTTPException(404, "Session digest not found")
    return digest


@app.get("/v1/digests/daily")
async def daily_digest_endpoint(
    date: str = Query(...),
    _auth: None = Depends(verify_token),
):
    assert _store
    digest = await _store.get_daily_digest(date)
    if not digest:
        raise HTTPException(404, "Daily digest not found")
    return digest


@app.get("/v1/digests/project")
async def project_digest_endpoint(
    project_key: str = Query(...),
    _auth: None = Depends(verify_token),
):
    assert _store
    digest = await _store.get_project_digest(project_key.strip().lower())
    if not digest:
        raise HTTPException(404, "Project digest not found")
    return digest


@app.get("/v1/stats")
async def stats_endpoint(_auth: None = Depends(verify_token)):
    assert _store
    return {"count": await _store.count(), "profile": _config.active_profile if _config else ""}


# === ASR SWITCH ===

@app.post("/v1/asr/switch")
async def switch_asr(model: str = Form(...), _auth: None = Depends(verify_token)):
    global _stt_override
    model = model.strip().lower()
    if model == "auto":
        _stt_override = None
        from runtime.routing.profiles import detect_profile, invalidate_cache
        invalidate_cache()
        assert _config
        await detect_profile(_config)
    else:
        if model not in STT_ROUTES:
            raise HTTPException(400, "Unknown STT route")
        _stt_override = model
    return {"model": _stt_override or "auto", "profile": _config.active_profile if _config else ""}


@app.get("/v1/detect")
async def detect_endpoint(_auth: None = Depends(verify_token)):
    assert _config
    from runtime.routing.profiles import detect_profile, invalidate_cache
    invalidate_cache()
    profile = await detect_profile(_config)
    p = _config.profile
    return {
        "env": profile,
        "asr_override": _stt_override,
        "route": {
            "asr_main": p.stt_main, "asr_fallback": p.stt_fallback,
            "llm_main": p.llm_main, "llm_fallback": p.llm_fallback,
        },
    }


# === DICTIONARY ===

@app.get("/v1/dictionary")
async def get_dict(_auth: None = Depends(verify_token)):
    return _load_terms()


@app.get("/v1/settings/providers")
async def get_provider_settings(_auth: None = Depends(verify_token)):
    return _settings_payload()


@app.get("/v1/settings/providers/test")
async def test_provider_settings(_auth: None = Depends(verify_token)):
    return _provider_connectivity_payload()


@app.get("/v1/settings/provider-qualifications")
async def get_provider_qualifications(_auth: None = Depends(verify_token)):
    assert _config
    return build_provider_qualification(_config)


@app.post("/v1/settings/providers")
async def update_provider_settings(request: Request, _auth: None = Depends(verify_token)):
    assert _config
    try:
        body = await request.json()
        apply_provider_settings_update(_config, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    return _settings_payload()


@app.post("/v1/digests/rebuild")
async def rebuild_digest_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    scope = str(body.get("scope", "")).strip().lower()
    return await _rebuild_digest(
        scope,
        session_id=str(body.get("session_id", "")).strip(),
        digest_date=str(body.get("date", "")).strip(),
        project_key=str(body.get("project_key", "")).strip(),
    )


@app.get("/v1/digests/export", response_class=PlainTextResponse)
async def export_digest_endpoint(
    scope: str = Query(...),
    session_id: str = Query(""),
    date: str = Query(""),
    project_key: str = Query(""),
    _auth: None = Depends(verify_token),
):
    return await _export_digest_markdown(
        scope.strip().lower(),
        session_id=session_id.strip(),
        digest_date=date.strip(),
        project_key=project_key.strip(),
    )


@app.post("/v1/integrations/obsidian/export")
async def export_obsidian_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    scope = str(body.get("scope", "")).strip().lower()
    vault_dir = str(body.get("vault_dir", "")).strip()
    if not vault_dir:
        raise HTTPException(400, "vault_dir required")
    if len(vault_dir) > 4096:
        raise HTTPException(400, "vault_dir is too long")
    vault_path = Path(vault_dir).expanduser()
    if not vault_path.is_absolute():
        raise HTTPException(400, "vault_dir must be an absolute path")
    session_id = str(body.get("session_id", "")).strip()
    digest_date = str(body.get("date", "")).strip()
    project_key = str(body.get("project_key", "")).strip()
    markdown = await _export_digest_markdown(
        scope,
        session_id=session_id,
        digest_date=digest_date,
        project_key=project_key,
    )
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: ObsidianSink(vault_path).export_digest(
            scope=scope,
            content=markdown,
            session_id=session_id,
            digest_date=digest_date,
            project_key=project_key,
        ),
    )
    return {
        "ok": True,
        "vault_path": result.vault_path,
        "note_path": result.note_path,
        "bytes_written": result.bytes_written,
    }


@app.post("/v1/integrations/obsidian/sync")
async def sync_obsidian_archive_endpoint(
    request: Request,
    _auth: None = Depends(verify_token),
) -> dict:
    """Archive recent normal-mode voice events into a user-selected vault."""

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    vault_dir = str(body.get("vault_dir", "")).strip()
    if not vault_dir:
        raise HTTPException(400, "vault_dir required")
    if len(vault_dir) > 4096:
        raise HTTPException(400, "vault_dir is too long")
    vault_path = Path(vault_dir).expanduser()
    if not vault_path.is_absolute():
        raise HTTPException(400, "vault_dir must be an absolute path")
    days = body.get("days", 7)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise HTTPException(400, "days must be an integer from 1 through 365")
    assert _config
    exported = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: sync_recent(_config.db_path, vault_path, days=days),
    )
    return {
        "exported": len(exported),
        "dates": exported,
        "output_dir": str(voice_log_dir(vault_path)),
    }












@app.post("/v1/integrations/ai-mate-memory/export")
async def export_ai_mate_memory_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    scope = str(body.get("scope", "")).strip().lower()
    base_dir = str(body.get("base_dir", "")).strip()
    if not base_dir:
        raise HTTPException(400, "base_dir required")
    session_id = str(body.get("session_id", "")).strip()
    digest_date = str(body.get("date", "")).strip()
    project_key = str(body.get("project_key", "")).strip()
    markdown = await _export_digest_markdown(
        scope,
        session_id=session_id,
        digest_date=digest_date,
        project_key=project_key,
    )
    sink = AIMateMemorySink(Path(base_dir))
    result = sink.export_digest(
        scope=scope,
        markdown=markdown,
        session_id=session_id,
        digest_date=digest_date,
        project_key=project_key,
    )
    return {
        "ok": True,
        "base_path": result.base_path,
        "record_path": result.record_path,
        "bytes_written": result.bytes_written,
    }

@app.post("/v1/dictionary")
async def update_dict(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    data = _load_terms()

    action = body.get("action")
    if action == "add":
        w, r = body.get("wrong", "").strip(), body.get("right", "").strip()
        if w and r:
            data["corrections"][w] = r
            if r not in data.get("preserve", []):
                data.setdefault("preserve", []).append(r)
    elif action == "delete":
        data["corrections"].pop(body.get("wrong", ""), None)

    return _write_terms(data)


# === RECENT UTTERANCE ===

@app.get("/v1/recent")
async def recent_endpoint(_auth: None = Depends(verify_token)):
    assert _recent
    item = _recent.latest()
    if not item:
        return {"recent": None}
    return {"recent": _recent_payload(item)}


@app.post("/v1/recent/retry")
async def retry_recent_endpoint(
    utterance_id: str = Form(...),
    provider: str = Form(""),
    _auth: None = Depends(verify_token),
):
    assert _recent and _config and _corrector
    provider = provider.strip().lower()
    if provider and provider not in STT_ROUTES:
        raise HTTPException(400, "Unknown STT route")
    item = _recent.get(utterance_id)
    if not item:
        raise HTTPException(404, "Recent utterance not found or expired")

    try:
        result, event = await fast_path(
            item.audio,
            _config,
            _corrector,
            source=item.event.source,
            agent=item.event.agent,
            target_app=item.event.target_app,
            session_id=item.event.session_id,
            recording_mode=item.event.recording_mode,
            stt_override=provider or None,
            role=item.event.role,
        )
    except STTError as e:
        raise HTTPException(502, str(e))

    enqueue(event)
    _recent.put(result, event, item.audio)
    payload = _recent_payload(_recent.latest())
    payload["retried_from"] = utterance_id
    return payload


@app.post("/v1/recent/mode")
async def recent_mode_endpoint(
    utterance_id: str = Form(...),
    mode: str = Form(...),
    _auth: None = Depends(verify_token),
):
    assert _recent and _store
    item = _recent.get(utterance_id)
    if not item:
        raise HTTPException(404, "Recent utterance not found or expired")
    if mode not in ("normal", "private", "ephemeral"):
        raise HTTPException(422, "Invalid mode")

    recording_mode = RecordingMode(mode)
    item.event.recording_mode = recording_mode
    item.event.export_status = ExportStatus.PENDING if recording_mode == RecordingMode.NORMAL else ExportStatus.SKIP
    await _store.update_mode(item.event.id, recording_mode)
    return {"utterance_id": utterance_id, "mode": recording_mode.value}


@app.post("/v1/recent/dictionary")
async def recent_dictionary_endpoint(
    utterance_id: str = Form(...),
    wrong: str = Form(...),
    right: str = Form(...),
    _auth: None = Depends(verify_token),
):
    assert _recent
    item = _recent.get(utterance_id)
    if not item:
        raise HTTPException(404, "Recent utterance not found or expired")

    wrong = wrong.strip()
    right = right.strip()
    if not wrong or not right:
        raise HTTPException(422, "Both wrong and right terms are required")

    data = _load_terms()
    data["corrections"][wrong] = right
    if right not in data.get("preserve", []):
        data.setdefault("preserve", []).append(right)
    _write_terms(data)
    return {"utterance_id": utterance_id, "wrong": wrong, "right": right, "dictionary": data}


@app.post("/v1/recent/dismiss")
async def recent_dismiss_endpoint(
    utterance_id: str = Form(...),
    _auth: None = Depends(verify_token),
):
    assert _recent
    ok = _recent.dismiss(utterance_id)
    if not ok:
        raise HTTPException(404, "Recent utterance not found or expired")
    return {"utterance_id": utterance_id, "dismissed": True}


# === OUTPUT ===

@app.post("/v1/output/undo")
async def output_undo_endpoint(
    output_id: str = Form(...),
    _auth: None = Depends(verify_token),
):
    assert _recent and _store
    item = _recent.get_by_output_id(output_id)
    if not item:
        raise HTTPException(404, "Output not found or expired")

    archive_recalled = False
    if item.event.recording_mode != RecordingMode.EPHEMERAL:
        archive_recalled = await _store.delete_event(item.event.id)

    return {
        "output_id": output_id,
        "utterance_id": item.event.utterance_id,
        "accepted": True,
        "archive_recalled": archive_recalled,
        "ui_undo_required": True,
        "mode": item.event.recording_mode.value,
    }


# === MAIN ===

def main():
    config = get_config()
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ])
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()


# === AGENT / GROUP / TASK ===

from runtime.models.agents import Agent, AgentType, Group, Task, OwnerIdentity

# In-memory store (persisted to JSON)
import json as _json
_LEGACY_JSON_DIR = legacy_voxlog_data_dirs()[0]


def _json_path(filename: str) -> Path:
    data_dir = _config.data_dir if _config else resolve_vox_agent_data_dir()
    return data_dir / filename


def _load_json(path: Path) -> list:
    if not path.exists() and path.parent == resolve_vox_agent_data_dir():
        legacy = _LEGACY_JSON_DIR / path.name
        if legacy.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(legacy.read_bytes())
    if path.exists():
        return _json.loads(path.read_text())
    return []

def _save_json(path: Path, data: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(data, ensure_ascii=False, indent=2, default=str))


@app.get("/v1/identity")
async def identity_endpoint():
    return OwnerIdentity().model_dump()


@app.get("/v1/agents/all")
async def all_agents_endpoint(_auth: None = Depends(verify_token)):
    return _load_json(_json_path("agents.json")) or [
        {"id": "claude-code", "name": "Claude Code", "emoji": "👨‍💻", "agent_type": "system_default", "parent_id": ""},
        {"id": "osborn", "name": "Osborn", "emoji": "🧠", "agent_type": "system_default", "parent_id": ""},
        {"id": "openclaw", "name": "OpenClaw", "emoji": "🦞", "agent_type": "system_default", "parent_id": ""},
        {"id": "general", "name": "General", "emoji": "📝", "agent_type": "system_default", "parent_id": ""},
    ]


@app.post("/v1/agents/create")
async def create_agent_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    agents = _load_json(_json_path("agents.json"))
    agent = Agent(name=body["name"], emoji=body.get("emoji", "🤖"),
                  agent_type=AgentType(body.get("type", "user_created")),
                  parent_id=body.get("parent_id", ""))
    agents.append(agent.model_dump())
    _save_json(_json_path("agents.json"), agents)
    return agent.model_dump()


@app.delete("/v1/agents/{agent_id}")
async def delete_agent_endpoint(agent_id: str, _auth: None = Depends(verify_token)):
    agents = _load_json(_json_path("agents.json"))
    agents = [a for a in agents if a["id"] != agent_id]
    _save_json(_json_path("agents.json"), agents)
    return {"deleted": agent_id}


@app.post("/v1/agents/{agent_id}/bind")
async def bind_agent_endpoint(agent_id: str, request: Request, _auth: None = Depends(verify_token)):
    """Bind an agent to a local 小龙虾 agent."""
    body = await request.json()
    agents = _load_json(_json_path("agents.json"))
    for a in agents:
        if a["id"] == agent_id:
            a["external_system"] = body.get("external_system", "xiaolongxia_local")
            a["external_agent_ref"] = body.get("external_agent_ref", "")
            a["binding_status"] = "bound"
            a["can_accept_tasks"] = body.get("can_accept_tasks", True)
            break
    _save_json(_json_path("agents.json"), agents)
    return {"bound": agent_id}


# --- Groups ---

@app.get("/v1/groups")
async def list_groups_endpoint(_auth: None = Depends(verify_token)):
    return _load_json(_json_path("groups.json"))


@app.post("/v1/groups/create")
async def create_group_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    groups = _load_json(_json_path("groups.json"))
    group = Group(title=body["title"], emoji=body.get("emoji", "👥"),
                  member_agent_ids=body.get("members", []))
    groups.append(group.model_dump())
    _save_json(_json_path("groups.json"), groups)
    return group.model_dump()


@app.delete("/v1/groups/{group_id}")
async def delete_group_endpoint(group_id: str, _auth: None = Depends(verify_token)):
    groups = _load_json(_json_path("groups.json"))
    groups = [g for g in groups if g["id"] != group_id]
    _save_json(_json_path("groups.json"), groups)
    return {"deleted": group_id}


# --- Tasks ---

@app.get("/v1/tasks")
async def list_tasks_endpoint(
    agent: str = Query(default=""),
    status: str = Query(default=""),
    _auth: None = Depends(verify_token),
):
    tasks = _load_json(_json_path("tasks.json"))
    if agent:
        tasks = [t for t in tasks if t.get("assigned_context_id") == agent]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return tasks


@app.post("/v1/tasks/create")
async def create_task_endpoint(request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    tasks = _load_json(_json_path("tasks.json"))
    task = Task(
        title=body["title"],
        description=body.get("description", ""),
        source_message_id=body.get("source_message_id", ""),
        assigned_context_id=body.get("assigned_to", ""),
        priority=body.get("priority", "medium"),
    )
    tasks.append(task.model_dump())
    _save_json(_json_path("tasks.json"), tasks)
    return task.model_dump()


@app.post("/v1/tasks/{task_id}/status")
async def update_task_status_endpoint(task_id: str, request: Request, _auth: None = Depends(verify_token)):
    body = await request.json()
    tasks = _load_json(_json_path("tasks.json"))
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = body["status"]
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
            break
    _save_json(_json_path("tasks.json"), tasks)
    return {"updated": task_id, "status": body["status"]}


# === OBSIDIAN SYNC ===

@app.post("/v1/sync-obsidian")
async def sync_obsidian_endpoint(
    days: int = Query(default=7, ge=1, le=365),
    _auth: None = Depends(verify_token),
) -> dict:
    assert _config
    exported = await asyncio.get_event_loop().run_in_executor(
        None, lambda: sync_recent(_config.db_path, DEFAULT_OBSIDIAN_VAULT, days=days)
    )
    return {"exported": len(exported), "dates": exported}
