"""Bounded, redacted Feishu capability probe. Offline dry-run is the default.

Only --execute enables a selected GET probe; it is not a substitute for owner
approval of each real request. No credentials are read on the dry-run path.
DM POST is construction-only, even with --execute. No retries or redirects.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import sys
import warnings
from typing import Any

import httpx

ORIGIN = "https://open.feishu.cn"


class ProbeError(Exception):
    """Static diagnostic category; never attach a response, URL, or secret."""


def _identifier(value: str | None, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_-]+", value):
        raise ProbeError("invalid_chat_id" if prefix == "oc_" else "invalid_user_id")
    return value


def dry_run(
    operation: str, *, chat_id: str | None = None, user_id: str | None = None, max_pages: int = 1
) -> dict[str, Any]:
    if (
        type(max_pages) is not int
        or not 1 <= max_pages <= 20
        or (operation in ("history", "dm") and max_pages != 1)
    ):
        raise ProbeError("invalid_page_budget")
    plan: dict[str, Any] = {
        "dry_run": True,
        "method": "GET",
        "live_get_supported": True,
        "requires_owner_approval": True,
        "max_pages": max_pages,
    }
    if operation == "chats":
        path, params = "/open-apis/im/v1/chats", {"page_size": 50, "user_id_type": "open_id"}
    elif operation == "members":
        chat_id = _identifier(chat_id, "oc_")
        path = f"/open-apis/im/v1/chats/{chat_id}/members"
        params = {"page_size": 50, "member_id_type": "open_id"}
    elif operation == "history":
        chat_id = _identifier(chat_id, "oc_")
        path = "/open-apis/im/v1/messages"
        params = {"container_id_type": "chat", "container_id": chat_id, "page_size": 50}
    elif operation == "dm":
        user_id = _identifier(user_id, "ou_")
        path, params = "/open-apis/im/v1/messages", {"receive_id_type": "open_id"}
        plan.update(
            method="POST",
            live_get_supported=False,
            json={
                "receive_id": user_id,
                "msg_type": "text",
                "content": json.dumps({"text": "Vox capability probe — synthetic only."}),
                "uuid": "REPLACE_WITH_STABLE_DELIVERY_UUID",
            },
        )
    else:
        raise ProbeError("invalid_operation")
    plan.update(path=path, params=params)
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return {**plan, "plan_sha256": digest}


def _get_page(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = None
    try:
        response = client.get(ORIGIN + path, params=params, follow_redirects=False)
    except Exception:
        pass
    if response is None:
        raise ProbeError("transport_error")
    # Classify these even when the gateway returned HTML, not JSON.
    if response.status_code == 429:
        raise ProbeError("rate_limited")
    if response.status_code == 401:
        raise ProbeError("authentication_failed")
    if response.status_code == 403:
        raise ProbeError("permission_denied")
    if response.is_redirect:
        raise ProbeError("redirect_refused")
    payload = None
    try:
        payload = response.json()
    except Exception:
        pass
    if not isinstance(payload, dict) or type(payload.get("code")) is not int:
        raise ProbeError("malformed_response" if response.is_success else "http_error")
    code = payload["code"]
    if code == 230020:
        raise ProbeError("rate_limited")
    if code in (230027, 99991672):
        raise ProbeError("permission_denied")
    if not response.is_success:
        raise ProbeError("http_error")
    if code != 0:
        raise ProbeError("api_error")
    data = payload.get("data")
    if (
        not isinstance(data, dict)
        or type(data.get("has_more")) is not bool
        or not isinstance(data.get("items"), list)
        or not all(isinstance(item, dict) for item in data["items"])
    ):
        raise ProbeError("malformed_response")
    if data["has_more"] and (not isinstance(data.get("page_token"), str) or not data["page_token"]):
        raise ProbeError("malformed_response")
    return data


def probe(
    client: httpx.Client, operation: str, *, chat_id: str | None = None, max_pages: int = 1
) -> dict[str, Any]:
    """Return counts only; a bounded list traversal never implies user authorization."""
    if operation == "dm":
        raise ProbeError("dry_run_only")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ProbeError("invalid_page_budget")
    plan = dry_run(operation, chat_id=chat_id, max_pages=max_pages)
    params = dict(plan["params"])
    result = {"operation": operation, "pages": 0, "items": 0, "complete": False}
    if operation == "history":
        result["sample_only"] = True
    if operation == "chats":
        result.update(external_chats=0, missing_boundary_fields=0)
    seen: set[str] = set()
    for _ in range(max_pages):
        data = _get_page(client, plan["path"], params)
        result["pages"] += 1
        result["items"] += len(data["items"])
        if operation == "chats":
            for item in data["items"]:
                result["external_chats"] += item.get("external") is True
                result["missing_boundary_fields"] += (
                    type(item.get("external")) is not bool
                    or not isinstance(item.get("tenant_key"), str)
                    or not item["tenant_key"]
                )
        result["complete"] = not data["has_more"]
        if result["complete"] or operation == "history":
            return result
        token = data["page_token"]
        if token in seen:
            raise ProbeError("pagination_cycle")
        seen.add(token)
        params["page_token"] = token
    raise ProbeError("page_budget_exhausted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation", choices=["chats", "members", "history", "dm"], default="chats"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument(
        "--execute", action="store_true", help="GET only, requires prior owner approval"
    )
    parser.add_argument(
        "--plan-sha256", help="digest from the reviewed dry-run; not owner authorization"
    )
    parser.add_argument("--chat-id")
    parser.add_argument("--user-id")
    parser.add_argument(
        "--max-pages", type=int, default=1, help="1–20, each page is another real request"
    )
    args = parser.parse_args(argv)
    try:
        plan = dry_run(
            args.operation, chat_id=args.chat_id, user_id=args.user_id, max_pages=args.max_pages
        )
        if not args.execute:
            print(json.dumps(plan, ensure_ascii=False))
            return 0
        if not plan["live_get_supported"]:
            raise ProbeError("dry_run_only")
        if args.plan_sha256 != plan["plan_sha256"]:
            raise ProbeError("plan_mismatch")
        if not 1 <= args.max_pages <= 20:
            raise ProbeError("invalid_page_budget")
        if not sys.stdin.isatty():
            raise ProbeError("interactive_token_required")
        # getpass can fall back to echoing input if terminal control fails.
        # Turn its warning into a failure before that fallback can read anything.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Tenant access token (hidden, memory only): ")
        if (
            not token
            or not token.isascii()
            or not token.isprintable()
            or any(char.isspace() for char in token)
        ):
            raise ProbeError("invalid_token")
        with httpx.Client(
            headers={"Authorization": "Bearer " + token},
            timeout=15,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            result = probe(client, args.operation, chat_id=args.chat_id, max_pages=args.max_pages)
        print(json.dumps(result))
        return 0
    except ProbeError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except (Exception, KeyboardInterrupt):
        # Never emit external response text, request headers, or a traceback.
        print(json.dumps({"error": "probe_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
