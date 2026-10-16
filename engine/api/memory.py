"""Voice-memory, capture, query, token, and companion-device routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from ..agent_answerer import get_answerer
from ..app_context import AppContext
from ..auth import Edition, Principal, require_admin, require_principal


class AddMemoryRequest(BaseModel):
    agent_id: str
    content: str
    source: str = "user"


BoundedNodeId = Annotated[str, Field(min_length=1, max_length=200)]


class AgentQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=4000)
    node_ids: list[BoundedNodeId] | None = Field(
        default=None,
        alias="nodeIds",
        max_length=50,
    )
    limit: int = Field(default=5, ge=1, le=20)
    answerer: str | None = Field(default=None, min_length=1, max_length=80)


def create_router(context: AppContext) -> APIRouter:
    """Bind memory and companion routes to one isolated context."""

    router = APIRouter(tags=["memory"])
    repo = context.voice_memory_repo
    audit = context.voice_audit_log

    def product_scope(principal: Principal) -> dict[str, str]:
        if context.auth_service.edition is Edition.PERSONAL:
            return {}
        return {
            "workspace_id": principal.org_id,
            "owner_user_id": principal.user_id,
        }

    # Keep specific memory-node routes ahead of the legacy {agent_id} route;
    # Starlette resolves equal-method paths in registration order.
    @router.post("/v1/memory/nodes")
    async def create_memory_node(
        payload: dict,
        _principal: Principal = Depends(require_admin),
    ):
        node = repo.add_memory_node(payload)
        audit.record(
            "memory.node.created",
            workspace_id=node.get("workspaceId"),
            target_id=node["id"],
            metadata={"type": node.get("type")},
        )
        return node

    @router.get("/v1/memory/nodes/{node_id}")
    async def get_memory_node(
        node_id: str = Path(min_length=1, max_length=200),
        principal: Principal = Depends(require_principal),
    ):
        node = repo.get_node(node_id, **product_scope(principal))
        if node is None:
            raise HTTPException(404, "memory node not found")
        return node

    @router.get("/v1/memory/search")
    async def search_memory(
        q: str = Query(default="", max_length=2000),
        limit: int = Query(default=20, ge=1, le=100),
        principal: Principal = Depends(require_principal),
    ):
        return repo.search_memory(q, limit=limit, **product_scope(principal))

    @router.get("/v1/memory/recent")
    async def recent_memory(
        days: int = Query(default=7, ge=1, le=3650),
        limit: int = Query(default=20, ge=1, le=100),
        principal: Principal = Depends(require_principal),
    ):
        return repo.list_recent_nodes(
            days=days,
            limit=limit,
            **product_scope(principal),
        )

    @router.get("/v1/memory/action-items")
    async def memory_action_items(
        status: str | None = Query(default=None, max_length=64),
        principal: Principal = Depends(require_principal),
    ):
        return repo.list_action_items(
            status=status,
            **product_scope(principal),
        )

    @router.get("/v1/memory/{agent_id}")
    async def get_memory(
        agent_id: str,
        _principal: Principal = Depends(require_admin),
    ):
        return [
            {
                "key": entry.key,
                "content": entry.content,
                "timestamp": entry.timestamp,
                "source": entry.source,
            }
            for entry in context.agent_memory.load(agent_id)
        ]

    @router.post("/v1/memory")
    async def add_memory(
        req: AddMemoryRequest,
        _principal: Principal = Depends(require_admin),
    ):
        context.agent_memory.add(req.agent_id, req.content, source=req.source)
        return {"ok": True}

    @router.delete("/v1/memory/{agent_id}")
    async def clear_memory(
        agent_id: str,
        _principal: Principal = Depends(require_admin),
    ):
        context.agent_memory.clear(agent_id)
        return {"ok": True}

    @router.get("/v1/memory")
    async def list_memory_agents(
        _principal: Principal = Depends(require_admin),
    ):
        return context.agent_memory.list_agents_with_memory()

    @router.post("/v1/agents/query")
    async def agent_query(
        payload: AgentQueryRequest,
        principal: Principal = Depends(require_principal),
    ):
        query = payload.query.strip()
        if not query:
            raise HTTPException(400, "query is required")
        node_ids = payload.node_ids
        if node_ids:
            nodes = []
            for node_id in node_ids:
                node = repo.get_node(node_id, **product_scope(principal))
                if node is None:
                    raise HTTPException(404, "memory node not found")
                nodes.append(node)
        else:
            nodes = [
                result["node"]
                for result in repo.search_memory(
                    query,
                    limit=payload.limit,
                    **product_scope(principal),
                )["results"]
            ]
        try:
            answerer = get_answerer(
                payload.answerer or context.model_policy.answerer
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            answer_text = answerer.answer(query, nodes)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        audit.record(
            "agent.query",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=None,
            metadata={"queryLength": len(query), "citations": len(nodes)},
        )
        return {
            "query": query,
            "answer": answer_text,
            "answerer": answerer.name,
            "citations": [
                {"id": node["id"], "title": node.get("title"), "type": node.get("type")}
                for node in nodes
            ],
            "contextNodeIds": [node["id"] for node in nodes],
        }

    @router.get("/v1/agents/context/recent")
    async def agent_recent_context(
        days: int = Query(default=7, ge=1, le=3650),
        limit: int = Query(default=20, ge=1, le=100),
        principal: Principal = Depends(require_principal),
    ):
        nodes = repo.list_recent_nodes(
            days=days,
            limit=limit,
            **product_scope(principal),
        )
        return {
            "days": days,
            "count": len(nodes),
            "nodes": [
                {
                    "id": node["id"],
                    "title": node.get("title"),
                    "type": node.get("type"),
                    "createdAt": node.get("createdAt"),
                }
                for node in nodes
            ],
        }

    @router.get("/v1/tokens/balance")
    async def get_token_balance(
        _principal: Principal = Depends(require_admin),
    ):
        return context.token_ledger.get_balance()

    @router.get("/v1/tokens/ledger")
    async def get_token_ledger(
        _principal: Principal = Depends(require_admin),
    ):
        return context.token_ledger.list_ledger()

    @router.post("/v1/devices/pairing-codes")
    async def create_device_pairing_code(
        _principal: Principal = Depends(require_admin),
    ):
        return context.device_simulator_repo.create_pairing_code()

    @router.post("/v1/devices/pair")
    async def pair_device(
        payload: dict,
        _principal: Principal = Depends(require_admin),
    ):
        try:
            device = context.device_simulator_repo.pair(
                payload.get("code"),
                payload.get("ownerUserId"),
                payload.get("workspaceId"),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                404, "pairing code invalid, expired, or already claimed"
            ) from exc
        audit.record(
            "device.paired",
            actor_id=device.get("ownerUserId"),
            workspace_id=device.get("workspaceId"),
            target_id=device["id"],
        )
        return device

    @router.get("/v1/devices")
    async def list_devices(
        _principal: Principal = Depends(require_admin),
    ):
        return context.device_simulator_repo.list_devices()

    @router.get("/v1/devices/{device_id}")
    async def get_device(
        device_id: str,
        _principal: Principal = Depends(require_admin),
    ):
        device = context.device_simulator_repo.get_device(device_id)
        if device is None:
            raise HTTPException(404, "device not found")
        return device

    @router.post("/v1/devices/{device_id}/events")
    async def add_device_event(
        device_id: str,
        payload: dict,
        principal: Principal = Depends(require_admin),
    ):
        try:
            event = context.device_simulator_repo.add_event(device_id, payload)
        except KeyError as exc:
            raise HTTPException(404, "device not found") from exc
        if event.get("type") == "RECORD_BUTTON_PRESSED":
            device = context.device_simulator_repo.get_device(device_id)
            session = repo.create_session({
                "workspaceId": principal.org_id,
                "ownerUserId": principal.user_id,
                "edition": context.auth_service.edition.value.upper(),
                "source": "HARDWARE",
                "mode": "MEETING",
                "languageHints": [],
                "privacyLevel": "PERSONAL",
                "consentState": "UNKNOWN",
                "deviceIds": [device_id],
                "localOnlyUntilSync": False,
            })
            event["sessionId"] = session["id"]
            audit.record(
                "capture.session.created",
                actor_id=principal.user_id,
                workspace_id=principal.org_id,
                target_id=session["id"],
                metadata={"deviceId": device["id"]},
            )
        return event

    @router.post("/v1/devices/{device_id}/tools")
    async def invoke_device_tool(
        device_id: str,
        payload: dict,
        _principal: Principal = Depends(require_admin),
    ):
        try:
            return context.device_simulator_repo.invoke_tool(device_id, payload)
        except KeyError as exc:
            raise HTTPException(404, "device not found") from exc

    return router
