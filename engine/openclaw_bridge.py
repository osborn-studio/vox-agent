"""OpenClaw Bridge — communicate with the local OpenClaw gateway.

OpenClaw (小龙虾) runs at 127.0.0.1:18789. This bridge:
1. Checks OpenClaw health
2. Sends tasks/messages to OpenClaw agents
3. Retrieves conversation history
4. Syncs agent status between Vox Agents and OpenClaw

When user talks to an OpenClaw-bound agent in Vox Agents,
the conversation is forwarded to OpenClaw's gateway API,
and appears in OpenClaw's web UI simultaneously.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY = "http://127.0.0.1:18789"


class OpenClawBridge:
    """Bridge between Vox Agents and the local OpenClaw gateway."""

    def __init__(self, gateway_url: str = DEFAULT_GATEWAY) -> None:
        self.gateway = gateway_url
        self._healthy: bool = False

    async def check_health(self) -> bool:
        """Check if OpenClaw gateway is running."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self.gateway}/health")
                self._healthy = r.status_code == 200
                return self._healthy
        except Exception:
            self._healthy = False
            return False

    @property
    def is_online(self) -> bool:
        return self._healthy

    async def send_message(
        self,
        agent_ref: str,
        message: str,
        workspace: str = "personal",
    ) -> dict:
        """Send a message to an OpenClaw agent.

        This creates a conversation entry in OpenClaw's system,
        visible in the OpenClaw web UI.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{self.gateway}/api/chat",
                    json={
                        "workspace": workspace,
                        "agent": agent_ref,
                        "message": message,
                    },
                )
                if r.status_code == 200:
                    return {"status": "sent", "response": r.json()}
                else:
                    return {"status": "error", "code": r.status_code, "body": r.text[:200]}
        except httpx.ConnectError:
            return {"status": "offline", "error": "OpenClaw not running"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def dispatch_task(
        self,
        title: str,
        prompt: str,
        agent_ref: str = "",
        workspace: str = "personal",
    ) -> dict:
        """Dispatch a task to OpenClaw for execution."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{self.gateway}/api/tasks",
                    json={
                        "title": title,
                        "prompt": prompt,
                        "workspace": workspace,
                        "agent": agent_ref,
                    },
                )
                if r.status_code in (200, 201):
                    return {"status": "dispatched", "data": r.json()}
                else:
                    return {"status": "error", "code": r.status_code}
        except httpx.ConnectError:
            return {"status": "offline"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_agents(self, workspace: str = "personal") -> list[dict]:
        """List agents from OpenClaw workspace."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{self.gateway}/api/agents",
                    params={"workspace": workspace},
                )
                if r.status_code == 200:
                    return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
        return []

    async def get_sessions(self, workspace: str = "personal", limit: int = 10) -> list[dict]:
        """Get recent conversation sessions from OpenClaw."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{self.gateway}/api/sessions",
                    params={"workspace": workspace, "limit": limit},
                )
                if r.status_code == 200:
                    return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
        return []

    async def open_web_ui(self) -> dict:
        """Open OpenClaw web UI in the default browser."""
        import subprocess
        try:
            subprocess.run(["open", self.gateway], check=True)
            return {"status": "opened", "url": self.gateway}
        except Exception as e:
            return {"status": "error", "error": str(e)}
