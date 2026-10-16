"""Minimal mobile bot gateway for VoxLog2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx
import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile


@dataclass
class GatewayConfig:
    upstream_base_url: str = "http://127.0.0.1:7901"
    upstream_api_token: str = ""
    gateway_api_token: str = ""
    request_timeout_seconds: float = 20.0
    default_source: str = "mobile-bot"


def load_gateway_config() -> GatewayConfig:
    import os

    return GatewayConfig(
        upstream_base_url=os.getenv("VOXLOG_GATEWAY_UPSTREAM_URL", "http://127.0.0.1:7901").rstrip("/"),
        upstream_api_token=os.getenv("VOXLOG_GATEWAY_UPSTREAM_API_TOKEN", "").strip(),
        gateway_api_token=os.getenv("VOXLOG_GATEWAY_API_TOKEN", "").strip(),
        request_timeout_seconds=float(os.getenv("VOXLOG_GATEWAY_TIMEOUT_SECONDS", "20")),
        default_source=os.getenv("VOXLOG_GATEWAY_DEFAULT_SOURCE", "mobile-bot").strip() or "mobile-bot",
    )


def create_app(
    config: GatewayConfig | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    gateway_config = config or load_gateway_config()
    app = FastAPI(title="VoxLog2 Gateway", version="0.1.0")

    async def verify_gateway_token(authorization: str | None = Header(None)) -> None:
        if not gateway_config.gateway_api_token:
            return
        if not authorization:
            raise HTTPException(401, "Missing auth")
        token = authorization.removeprefix("Bearer ").strip()
        if token != gateway_config.gateway_api_token:
            raise HTTPException(401, "Invalid token")

    def _upstream_headers() -> dict[str, str]:
        headers: dict[str, str] = {}
        if gateway_config.upstream_api_token:
            headers["Authorization"] = f"Bearer {gateway_config.upstream_api_token}"
        return headers

    def _build_client() -> httpx.AsyncClient:
        if client_factory:
            return client_factory()
        return httpx.AsyncClient(
            base_url=gateway_config.upstream_base_url,
            timeout=gateway_config.request_timeout_seconds,
        )

    @app.get("/health")
    async def health(_auth: None = Depends(verify_gateway_token)):
        return {
            "status": "ok",
            "service": "gateway",
            "upstream_base_url": gateway_config.upstream_base_url,
            "default_source": gateway_config.default_source,
        }

    @app.post("/v1/gateway/text")
    async def ingest_text(request: Request, _auth: None = Depends(verify_gateway_token)):
        body = await request.json()
        payload = {
            "text": str(body.get("text", "")).strip(),
            "source": str(body.get("source", gateway_config.default_source)).strip() or gateway_config.default_source,
            "agent": str(body.get("agent", "")).strip(),
            "target_app": str(body.get("target_app", "")).strip() or "mobile",
            "session_id": str(body.get("session_id", "")).strip(),
            "mode": str(body.get("mode", "normal")).strip().lower() or "normal",
            "role": str(body.get("role", "other")).strip() or "other",
        }
        if not payload["text"]:
            raise HTTPException(422, "text required")
        async with _build_client() as client:
            response = await client.post(
                "/v1/voice/text",
                json=payload,
                headers=_upstream_headers(),
            )
            response.raise_for_status()
            return response.json()

    @app.post("/v1/gateway/audio")
    async def ingest_audio(
        audio: UploadFile = File(...),
        source: str = Form(""),
        agent: str = Form(""),
        target_app: str = Form("mobile"),
        session_id: str = Form(""),
        mode: str = Form("normal"),
        _auth: None = Depends(verify_gateway_token),
    ):
        content = await audio.read()
        files = {
            "audio": (audio.filename or "gateway-upload.bin", content, audio.content_type or "application/octet-stream"),
        }
        data = {
            "source": source.strip() or gateway_config.default_source,
            "env": "mobile",
            "agent": agent.strip(),
            "target_app": target_app.strip() or "mobile",
            "session_id": session_id.strip(),
            "mode": mode.strip().lower() or "normal",
        }
        async with _build_client() as client:
            response = await client.post(
                "/v1/voice",
                data=data,
                files=files,
                headers=_upstream_headers(),
            )
            response.raise_for_status()
            return response.json()

    return app


app = create_app()


def main() -> None:
    gateway_config = load_gateway_config()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7903,
        reload=False,
        log_level="info",
        access_log=False,
    )
