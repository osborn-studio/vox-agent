#!/usr/bin/env python3
"""Validate a tester-shared Mobile diagnostic bundle without echoing content."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

MAX_BUNDLE_BYTES = 32_768
MAX_REQUESTS = 20
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
SAFE_TEXT = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
SENSITIVE_CONTENT = re.compile(
    r"(authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"https?://[^/\s:@]+:[^@\s/]+@|"
    r"(?:api[_-]?key|password|secret)\s*(?:[:=]\s*|\s+)\S+)",
    re.IGNORECASE,
)

TOP_LEVEL_KEYS = {
    "schema",
    "generatedAt",
    "app",
    "device",
    "latest",
    "recentRequests",
    "crashReporting",
}
APP_KEYS = {"version", "buildVersion", "executionEnvironment"}
DEVICE_KEYS = {"os", "osVersion", "deviceClass"}
LATEST_KEYS = {"networkState", "endpointClass", "errorCode", "correlationId"}
REQUEST_KEYS = {
    "occurredAt",
    "correlationId",
    "endpointClass",
    "networkState",
    "errorCode",
}
NETWORK_STATES = {"reachable", "offline"}
ENDPOINT_CLASSES = {
    "loopback",
    "private-lan",
    "remote-https",
    "remote-http",
    "invalid",
}
DEVICE_CLASSES = {"phone", "tablet", "desktop", "tv", "unknown"}


def _exact_object(value: Any, keys: set[str], finding: str) -> list[str]:
    return (
        []
        if isinstance(value, dict) and set(value) == keys
        else [finding]
    )


def _safe_text(value: Any) -> bool:
    return isinstance(value, str) and SAFE_TEXT.fullmatch(value) is not None


def validate_bundle(payload: Any, *, serialized_bytes: int | None = None) -> list[str]:
    """Return bounded finding codes only; never return payload excerpts."""

    findings: list[str] = []
    if serialized_bytes is not None and serialized_bytes > MAX_BUNDLE_BYTES:
        findings.append("bundle_too_large")
    findings.extend(_exact_object(payload, TOP_LEVEL_KEYS, "invalid_top_level"))
    if not isinstance(payload, dict):
        return findings
    if payload.get("schema") != "voxagent.mobile.diagnostics.v1":
        findings.append("invalid_schema")
    if not isinstance(payload.get("generatedAt"), str) or not TIMESTAMP.fullmatch(
        payload["generatedAt"]
    ):
        findings.append("invalid_generated_at")
    if payload.get("crashReporting") != "not-configured-manual-diagnostics-only":
        findings.append("invalid_crash_reporting")

    app = payload.get("app")
    findings.extend(_exact_object(app, APP_KEYS, "invalid_app"))
    if isinstance(app, dict) and not all(_safe_text(app.get(key)) for key in APP_KEYS):
        findings.append("invalid_app_value")

    device = payload.get("device")
    findings.extend(_exact_object(device, DEVICE_KEYS, "invalid_device"))
    if isinstance(device, dict):
        if not _safe_text(device.get("os")) or not _safe_text(device.get("osVersion")):
            findings.append("invalid_device_value")
        if device.get("deviceClass") not in DEVICE_CLASSES:
            findings.append("invalid_device_class")

    latest = payload.get("latest")
    findings.extend(_exact_object(latest, LATEST_KEYS, "invalid_latest"))
    if isinstance(latest, dict):
        if latest.get("networkState") not in NETWORK_STATES | {"unknown"}:
            findings.append("invalid_network_state")
        if latest.get("endpointClass") not in ENDPOINT_CLASSES | {"unknown"}:
            findings.append("invalid_endpoint_class")
        if latest.get("errorCode") != "NO_REQUEST" and not ERROR_CODE.fullmatch(
            str(latest.get("errorCode", ""))
        ):
            findings.append("invalid_error_code")
        correlation_id = latest.get("correlationId")
        if correlation_id != "none" and not REQUEST_ID.fullmatch(str(correlation_id)):
            findings.append("invalid_correlation_id")

    requests = payload.get("recentRequests")
    if not isinstance(requests, list):
        findings.append("invalid_requests")
    else:
        if len(requests) > MAX_REQUESTS:
            findings.append("too_many_requests")
        for event in requests:
            findings.extend(_exact_object(event, REQUEST_KEYS, "invalid_request"))
            if not isinstance(event, dict):
                continue
            if not isinstance(event.get("occurredAt"), str) or not TIMESTAMP.fullmatch(
                event["occurredAt"]
            ):
                findings.append("invalid_request_time")
            if not REQUEST_ID.fullmatch(str(event.get("correlationId", ""))):
                findings.append("invalid_correlation_id")
            if event.get("endpointClass") not in ENDPOINT_CLASSES:
                findings.append("invalid_endpoint_class")
            if event.get("networkState") not in NETWORK_STATES:
                findings.append("invalid_network_state")
            if not ERROR_CODE.fullmatch(str(event.get("errorCode", ""))):
                findings.append("invalid_error_code")

    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if SENSITIVE_CONTENT.search(serialized):
        findings.append("sensitive_content_pattern")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Vox Agent Mobile diagnostic JSON bundle."
    )
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    raw = args.bundle.read_bytes()
    if len(raw) > MAX_BUNDLE_BYTES:
        print("FAIL bundle_too_large")
        return 1
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("FAIL invalid_json")
        return 1
    findings = validate_bundle(payload, serialized_bytes=len(raw))
    if findings:
        print("FAIL " + ",".join(findings))
        return 1
    print("PASS privacy-safe Mobile diagnostic bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
