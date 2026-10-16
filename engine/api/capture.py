"""Principal-scoped capture session routes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from ..app_context import AppContext
from ..asr import get_asr_adapter
from ..auth import Edition, Principal, require_principal
from ..voice_memory import now_iso

_DEFAULT_MAX_AUDIO_UPLOAD_BYTES = 100 * 1024 * 1024


def create_router(context: AppContext) -> APIRouter:
    """Bind capture routes to one context and verified product principal."""

    router = APIRouter(tags=["capture"])
    repo = context.voice_memory_repo
    audit = context.voice_audit_log

    def memory_scope(principal: Principal) -> dict[str, str]:
        if context.auth_service.edition is Edition.PERSONAL:
            return {}
        return {
            "workspace_id": principal.org_id,
            "owner_user_id": principal.user_id,
        }

    def owned_session(session_id: str, principal: Principal) -> dict:
        session = repo.get_session(session_id)
        if (
            session is None
            or session.get("workspaceId") != principal.org_id
            or session.get("ownerUserId") != principal.user_id
        ):
            raise HTTPException(404, "capture session not found")
        return session

    @router.post("/v1/capture/sessions")
    async def create_capture_session(
        payload: dict,
        principal: Principal = Depends(require_principal),
    ):
        trusted_payload = {
            **payload,
            "workspaceId": principal.org_id,
            "ownerUserId": principal.user_id,
            "edition": context.auth_service.edition.value.upper(),
        }
        session = repo.create_session(trusted_payload)
        audit.record(
            "capture.session.created",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=session["id"],
        )
        return session

    @router.get("/v1/capture/sessions")
    async def list_capture_sessions(
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = Depends(require_principal),
    ):
        return repo.list_sessions(
            limit=limit,
            workspace_id=principal.org_id,
            owner_user_id=principal.user_id,
        )

    @router.get("/v1/capture/sessions/{session_id}")
    async def get_capture_session(
        session_id: str,
        principal: Principal = Depends(require_principal),
    ):
        return owned_session(session_id, principal)

    @router.patch("/v1/capture/sessions/{session_id}")
    async def patch_capture_session(
        session_id: str,
        payload: dict,
        principal: Principal = Depends(require_principal),
    ):
        owned_session(session_id, principal)
        return repo.update_session(session_id, payload)

    @router.post("/v1/capture/sessions/{session_id}/transcript-segments")
    async def add_transcript_segment(
        session_id: str,
        payload: dict,
        principal: Principal = Depends(require_principal),
    ):
        owned_session(session_id, principal)
        return repo.add_segment(session_id, payload)

    @router.get("/v1/capture/sessions/{session_id}/transcript-segments")
    async def list_transcript_segments(
        session_id: str,
        principal: Principal = Depends(require_principal),
    ):
        owned_session(session_id, principal)
        return repo.list_segments(session_id)

    @router.post("/v1/capture/sessions/{session_id}/audio")
    async def upload_capture_audio(
        session_id: str,
        principal: Principal = Depends(require_principal),
        file: UploadFile = File(...),
    ):
        owned_session(session_id, principal)
        audio_dir = _audio_dir()
        audio_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "").suffix or ".wav"
        destination = audio_dir / f"{session_id}{suffix}"
        max_bytes = _max_audio_upload_bytes()
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, "capture audio exceeds the upload limit")
        destination.write_bytes(data)
        repo.set_audio_path(session_id, str(destination))
        audit.record(
            "capture.audio.uploaded",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=session_id,
            metadata={"bytes": len(data)},
        )
        return {
            "sessionId": session_id,
            "audioPath": str(destination),
            "bytes": len(data),
        }

    @router.post("/v1/capture/sessions/{session_id}/process")
    async def process_capture_session(
        session_id: str,
        payload: dict | None = None,
        principal: Principal = Depends(require_principal),
    ):
        session = owned_session(session_id, principal)
        body = payload or {}
        if (
            body.get("audioPath")
            and context.auth_service.edition.value == "enterprise"
        ):
            raise HTTPException(
                400,
                "enterprise capture processing requires session-uploaded audio",
            )
        if not repo.list_segments(session_id):
            adapter = get_asr_adapter(
                body.get("asrAdapter") or context.model_policy.asr_adapter
            )
            audio_path = body.get("audioPath") or repo.get_audio_path(session_id)
            for segment_payload in adapter.transcribe(
                session_id=session_id,
                audio_path=audio_path,
                language_hints=session.get("languageHints", []),
            ):
                repo.add_segment(session_id, segment_payload)

        segments = repo.list_segments(session_id)
        detected = sorted(
            {segment["language"] for segment in segments if segment.get("language")}
        )
        if not repo.list_nodes_for_session(
            session_id,
            node_type="SUMMARY",
            **memory_scope(principal),
        ):
            lines = [
                segment.get("textNormalized") or segment.get("textRaw") or ""
                for segment in segments
            ]
            repo.add_memory_node({
                "workspaceId": principal.org_id,
                "ownerUserId": principal.user_id,
                "sourceSessionId": session_id,
                "type": "SUMMARY",
                "title": f"Summary: {session.get('title') or session_id}",
                "body": " ".join(line for line in lines if line)[:2000],
                "tags": ["auto-summary"],
                "confidence": 0.9,
            })
        return repo.update_session(session_id, {
            "status": "READY",
            "detectedLanguages": detected,
            "endedAt": session.get("endedAt") or now_iso(),
        })

    @router.get("/v1/capture/sessions/{session_id}/summary")
    async def get_capture_session_summary(
        session_id: str,
        principal: Principal = Depends(require_principal),
    ):
        session = owned_session(session_id, principal)
        return {
            "sessionId": session_id,
            "status": session["status"],
            "summaryNodes": repo.list_nodes_for_session(
                session_id,
                node_type="SUMMARY",
                **memory_scope(principal),
            ),
            "actionItems": repo.list_nodes_for_session(
                session_id,
                node_type="ACTION_ITEM",
                **memory_scope(principal),
            ),
            "decisions": repo.list_nodes_for_session(
                session_id,
                node_type="DECISION",
                **memory_scope(principal),
            ),
        }

    return router


def _audio_dir() -> Path:
    from runtime.data_paths import resolve_vox_agent_data_dir

    return Path(
        os.environ.get(
            "VOXAGENT_AUDIO_DIR", str(resolve_vox_agent_data_dir() / "audio")
        )
    )


def _max_audio_upload_bytes() -> int:
    raw = os.environ.get(
        "VOXAGENT_MAX_AUDIO_UPLOAD_BYTES",
        str(_DEFAULT_MAX_AUDIO_UPLOAD_BYTES),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_AUDIO_UPLOAD_BYTES
