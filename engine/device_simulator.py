"""In-memory simulated device protocol for the VOXAGENT mobile companion.

Simulates a paired hardware device (e.g. a VOXPIN recorder) without any
real hardware: pairing codes, devices, device events, and tool
invocations. Pure in-memory, no lifespan dependency -- mirrors
engine/voice_memory.py's test-isolation story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .voice_memory import now_iso

_PAIRING_CODE_TTL_MINUTES = 10


@dataclass
class DeviceSimulatorRepository:
    pairing_codes: dict = field(default_factory=dict)
    devices: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)  # device_id -> list[dict]
    tools: dict = field(default_factory=dict)  # device_id -> list[dict]

    # -- Pairing ------------------------------------------------------------

    def create_pairing_code(self) -> dict:
        code = uuid4().hex[:8].upper()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_PAIRING_CODE_TTL_MINUTES)).isoformat()
        record = {
            "code": code,
            "qrPayload": f"voxagent://pair?code={code}",
            "expiresAt": expires_at,
            "claimed": False,
        }
        self.pairing_codes[code] = record
        return {"code": code, "qrPayload": record["qrPayload"], "expiresAt": expires_at}

    def pair(self, code: str, owner_user_id: str, workspace_id: str) -> dict:
        record = self.pairing_codes.get(code)
        if record is None:
            raise KeyError(code)
        if record["claimed"]:
            raise ValueError("pairing code already claimed")
        if datetime.fromisoformat(record["expiresAt"]) < datetime.now(timezone.utc):
            raise ValueError("pairing code expired")

        record["claimed"] = True

        timestamp = now_iso()
        device_id = f"dev_{uuid4().hex[:12]}"
        device = {
            "id": device_id,
            "workspaceId": workspace_id,
            "ownerUserId": owner_user_id,
            "kind": "SIMULATOR",
            "displayName": "Simulated VOXPIN",
            "status": "CONNECTED",
            "batteryPercent": 100,
            "firmwareVersion": "0.1.0",
            "pairedAt": timestamp,
            "lastSeenAt": timestamp,
        }
        self.devices[device_id] = device
        self.events[device_id] = []
        self.tools[device_id] = []
        return device

    # -- Devices --------------------------------------------------------------

    def list_devices(self) -> list:
        return list(self.devices.values())

    def get_device(self, device_id: str) -> dict | None:
        return self.devices.get(device_id)

    # -- Events ---------------------------------------------------------------

    def add_event(self, device_id: str, payload: dict) -> dict:
        if device_id not in self.devices:
            raise KeyError(device_id)
        event = {
            "occurredAt": now_iso(),
            **payload,
            "id": f"evt_{uuid4().hex[:12]}",
            "deviceId": device_id,
            "signature": None,
            "sessionId": None,
        }
        self.events[device_id].append(event)
        self.devices[device_id]["lastSeenAt"] = now_iso()
        return event

    def list_events(self, device_id: str) -> list:
        if device_id not in self.devices:
            raise KeyError(device_id)
        return self.events.get(device_id, [])

    # -- Tools ------------------------------------------------------------------

    def invoke_tool(self, device_id: str, payload: dict) -> dict:
        if device_id not in self.devices:
            raise KeyError(device_id)
        invocation = {
            **payload,
            "id": f"tool_{uuid4().hex[:12]}",
            "deviceId": device_id,
            "requestedAt": now_iso(),
            "status": "ACKNOWLEDGED",
        }
        self.tools[device_id].append(invocation)
        return invocation
