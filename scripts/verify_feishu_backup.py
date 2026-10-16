#!/usr/bin/env python3
"""Opt-in synthetic Feishu group probe. Default execution is entirely offline.

Live mode sends the same synthetic message twice using one idempotency key and
reads back only that message. It never prints unrelated conversation content.
A passed result proves only this run's send/replay/readback, not 1h deduplication,
SSO, production authorization, durable queuing, or deployment readiness.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import uuid4


class ProbeError(RuntimeError):
    """Safe fixed-category failure; never echo CLI output containing user data."""


def unwrap(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ProbeError("unexpected_cli_response")
    if value.get("ok") is False or value.get("code", 0) != 0:
        raise ProbeError("cli_request_failed")
    result = value.get("data", value)
    if not isinstance(result, dict):
        raise ProbeError("unexpected_cli_response")
    return result


def cli(args: list[str]) -> dict:
    try:
        result = subprocess.run(
            ["lark-cli", *args], capture_output=True, text=True, timeout=30, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("delivery_unknown_timeout_do_not_blindly_repeat") from exc
    except OSError as exc:
        raise ProbeError("cli_unavailable") from exc
    if result.returncode:
        raise ProbeError("cli_failed_delivery_may_be_unknown")
    try:
        return unwrap(json.loads(result.stdout))
    except (ValueError, TypeError) as exc:
        raise ProbeError("unexpected_cli_response_delivery_may_be_unknown") from exc


def run_probe(chat_id: str, *, live: bool = False, runner=cli, max_pages: int = 5) -> dict:
    evidence = {}
    try:
        return _run_probe(chat_id, live=live, runner=runner, max_pages=max_pages, evidence=evidence)
    except ProbeError as exc:
        exc.evidence = evidence
        raise


def _run_probe(chat_id, *, live, runner, max_pages, evidence):
    if not re.fullmatch(r"oc_[A-Za-z0-9_-]+", chat_id):
        raise ValueError("An explicit group chat_id (oc_...) is required")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("max_pages must be 1..20")
    event_id = str(uuid4())
    started = datetime.now(timezone.utc)
    payload = f"Vox backup synthetic capability probe\nevent_id={event_id}\nNo employee content."
    send = ["im", "+messages-send", "--as", "bot", "--chat-id", chat_id,
            "--text", payload, "--idempotency-key", event_id]
    evidence.update({
        "schema_version": 1, "status": "preview_only", "verified": False,
        "event_id": event_id, "started_at": started.isoformat(),
        "scope": "synthetic_group_send_immediate_replay_readback",
        "not_verified": ["one_hour_boundary", "queue_recovery", "employee_identity", "production"],
    })
    if not live:
        evidence["preview_command"] = ["lark-cli", *send, "--dry-run"]
        return evidence
    first = unwrap(runner(send))
    message_id = first.get("message_id")
    if not isinstance(message_id, str) or not message_id.startswith("om_"):
        raise ProbeError("missing_message_id_delivery_unknown")
    if first.get("chat_id") != chat_id:
        raise ProbeError("unexpected_destination")
    evidence["message_id"] = message_id
    second = unwrap(runner(send))
    if second.get("message_id") != message_id or second.get("chat_id") != chat_id:
        return {**evidence, "status": "idempotency_failed"}
    cursor = None
    matching_ids = set()
    original_found = False
    seen = set()
    for page in range(max_pages):
        args = ["im", "+chat-messages-list", "--as", "bot", "--chat-id", chat_id,
                "--start", (started - timedelta(minutes=1)).isoformat(),
                "--order", "desc", "--page-size", "50", "--no-reactions"]
        if cursor:
            args += ["--page-token", cursor]
        response = unwrap(runner(args))
        messages = response.get("messages")
        if not isinstance(messages, list):
            raise ProbeError("unexpected_history_schema")
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            # CLI text may be direct or a JSON-serialized text-message body.
            if isinstance(content, str):
                try:
                    decoded = json.loads(content)
                    if isinstance(decoded, dict):
                        content = decoded.get("text")
                except ValueError:
                    pass
            elif isinstance(content, dict):
                content = content.get("text")
            if isinstance(content, str) and f"event_id={event_id}" in content:
                matching_ids.add(message.get("message_id"))
            if message.get("message_id") == message_id:
                if content != payload or message.get("deleted"):
                    return {**evidence, "status": "readback_mismatch"}
                original_found = True
        if not response.get("has_more"):
            if len(matching_ids) > 1:
                return {**evidence, "status": "duplicate_messages"}
            if original_found and matching_ids == {message_id}:
                return {**evidence, "status": "passed", "verified": True, "pages_read": page + 1}
            return {**evidence, "status": "readback_missing"}
        cursor = response.get("page_token")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ProbeError("invalid_pagination")
        seen.add(cursor)
    return {**evidence, "status": "readback_incomplete"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--live", action="store_true", help="Send synthetic data to this group")
    args = parser.parse_args()
    try:
        result = run_probe(args.chat_id, live=args.live)
    except (ValueError, ProbeError) as exc:
        print(json.dumps({**getattr(exc, "evidence", {}), "verified": False,
                          "status": "failed", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"preview_only", "passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
