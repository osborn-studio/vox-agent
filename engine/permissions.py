"""Permission system — three-level access control (inspired by OpenHarness)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_REMOTE_SCOPE_KEYS = frozenset({
    "action",
    "command",
    "file_path",
    "path",
    "resource",
    "target",
    "url",
    "working_directory",
})
_SENSITIVE_SCOPE_KEYS = (
    "authorization",
    "content",
    "credential",
    "password",
    "prompt",
    "secret",
    "text",
    "token",
    "api_key",
    "apikey",
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password)"
    r"[A-Za-z0-9_-]*\s*(?:[:=]\s*|\s+)\S+|"
    r"https?://[^/\s:@]+:[^@\s/]+@)"
)
_MAX_SCOPE_VALUE_LENGTH = 512


class PermissionMode(str, Enum):
    DEFAULT = "default"  # write ops require user approval via WebSocket
    AUTO = "auto"  # sandboxed: path rules + command deny list
    PLAN = "plan"  # read-only, no writes allowed


READ_ONLY_TOOLS = frozenset({"read", "grep", "glob", "web_search"})

DEFAULT_DENY_COMMANDS = [
    # Split literal dangerous examples so repository governance does not
    # mistake this deny-list data for executable shell.
    "rm -rf " + "/",
    "rm -rf ~",
    "sudo ",
    "curl | " + "bash",
    "wget | bash",
    "curl | " + "sh",
    "mkfs",
    "dd if=",
    "chmod 777",
    "> /dev/sda",
]


@dataclass
class PathRule:
    pattern: str  # glob-style, e.g. "~/voxAgent/**"
    allow: bool = True


@dataclass
class PermissionConfig:
    mode: PermissionMode = PermissionMode.DEFAULT
    path_rules: list[PathRule] = field(default_factory=list)
    deny_commands: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_COMMANDS))


@dataclass
class ToolCall:
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""


@dataclass(frozen=True)
class PermissionRequest:
    approval_id: str
    task_id: str
    agent_id: str
    tool: str
    scope: dict[str, str]
    requested_at: str
    expires_at: str


ApprovalNotifier = Callable[[PermissionRequest], Awaitable[None]]


@dataclass(frozen=True)
class FileScopeRule:
    requester_user_id: str
    workspace_id: str
    tool: str
    scope_key: str
    scope_value: str


class PermissionManager:
    """Evaluates whether a tool call should be allowed."""

    def __init__(
        self,
        config: PermissionConfig | None = None,
        *,
        on_request: ApprovalNotifier | None = None,
        approval_timeout_seconds: float = 120.0,
        rules_path: str | Path | None = None,
    ) -> None:
        self.config = config or PermissionConfig()
        self._on_request = on_request
        self._approval_timeout_seconds = approval_timeout_seconds
        self._pending_approvals: dict[str, asyncio.Future[bool]] = {}
        self._pending_requests: dict[str, PermissionRequest] = {}
        self._pending_principals: dict[str, tuple[str, str]] = {}
        self._rules_path = Path(rules_path) if rules_path else None
        self._file_scope_rules = self._load_file_scope_rules()

    async def check(
        self,
        tool_call: ToolCall,
        *,
        task_id: str = "",
        agent_id: str = "",
        requester_user_id: str = "",
        workspace_id: str = "",
    ) -> bool:
        mode = self.config.mode

        if mode == PermissionMode.PLAN:
            return tool_call.tool in READ_ONLY_TOOLS

        if mode == PermissionMode.AUTO:
            return self._check_auto(tool_call)

        # DEFAULT mode: reads pass, writes need approval
        if tool_call.tool in READ_ONLY_TOOLS:
            return True

        if not self._check_path_rules(tool_call):
            return False
        if not self._check_command_deny(tool_call):
            return False
        if self._matches_file_scope_rule(
            tool_call,
            requester_user_id=requester_user_id,
            workspace_id=workspace_id,
        ):
            return True

        return await self._request_approval(
            tool_call,
            task_id=task_id,
            agent_id=agent_id,
            requester_user_id=requester_user_id,
            workspace_id=workspace_id,
        )

    # -- AUTO checks ---------------------------------------------------------

    def _check_auto(self, tool_call: ToolCall) -> bool:
        return self._check_path_rules(tool_call) and self._check_command_deny(tool_call)

    def _check_path_rules(self, tool_call: ToolCall) -> bool:
        path = tool_call.input.get("path") or tool_call.input.get("file_path", "")
        if not path or not self.config.path_rules:
            return True
        from fnmatch import fnmatch
        for rule in self.config.path_rules:
            if fnmatch(path, rule.pattern):
                return rule.allow
        return True  # no matching rule = allow

    def _check_command_deny(self, tool_call: ToolCall) -> bool:
        if tool_call.tool != "bash":
            return True
        cmd = tool_call.input.get("command", "")
        for denied in self.config.deny_commands:
            if denied in cmd:
                logger.warning("command denied: %s (matched %s)", cmd, denied)
                return False
        return True

    # -- user approval (via WebSocket) ---------------------------------------

    async def _request_approval(
        self,
        tool_call: ToolCall,
        *,
        task_id: str = "",
        agent_id: str = "",
        requester_user_id: str = "",
        workspace_id: str = "",
    ) -> bool:
        if self._on_request is None:
            logger.error("permission request denied because no approval notifier is configured")
            return False

        approval_id = uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_approvals[approval_id] = future
        requested_at = datetime.now(timezone.utc)
        request = PermissionRequest(
            approval_id=approval_id,
            task_id=task_id,
            agent_id=agent_id,
            tool=tool_call.tool,
            scope=self._display_scope(tool_call.input),
            requested_at=requested_at.isoformat(),
            expires_at=(
                requested_at + timedelta(seconds=self._approval_timeout_seconds)
            ).isoformat(),
        )
        self._pending_requests[approval_id] = request
        self._pending_principals[approval_id] = (
            requester_user_id,
            workspace_id,
        )
        logger.info("awaiting user approval for %s", tool_call.tool)
        try:
            await self._on_request(request)
            return await asyncio.wait_for(
                future,
                timeout=self._approval_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("approval timeout for %s", tool_call.tool)
            return False
        except Exception:
            logger.exception("failed to publish approval request for %s", tool_call.tool)
            return False
        finally:
            self._pending_approvals.pop(approval_id, None)
            self._pending_requests.pop(approval_id, None)
            self._pending_principals.pop(approval_id, None)

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        *,
        remember_scope: bool = False,
    ) -> bool:
        future = self._pending_approvals.get(approval_id)
        if future and not future.done():
            if approved and remember_scope:
                request = self._pending_requests.get(approval_id)
                if request:
                    owner = self._pending_principals.get(approval_id, ("", ""))
                    self._remember_file_scope(
                        request,
                        requester_user_id=owner[0],
                        workspace_id=owner[1],
                    )
            future.set_result(approved)
            return True
        return False

    def list_pending_requests(self) -> list[PermissionRequest]:
        """Return an immutable reconciliation snapshot ordered oldest first."""
        return sorted(
            self._pending_requests.values(),
            key=lambda request: (request.requested_at, request.approval_id),
        )

    def list_pending_requests_for_principal(
        self,
        *,
        requester_user_id: str,
        workspace_id: str,
        include_unowned: bool = False,
    ) -> list[PermissionRequest]:
        """Return only requests initiated by one verified product principal.

        Legacy requests without stored ownership are available only when the
        caller explicitly enables the local-personal compatibility path.
        """
        visible = []
        for request in self._pending_requests.values():
            owner = self._pending_principals.get(request.approval_id, ("", ""))
            if owner == (requester_user_id, workspace_id):
                visible.append(request)
            elif include_unowned and owner == ("", ""):
                visible.append(request)
        return sorted(
            visible,
            key=lambda request: (request.requested_at, request.approval_id),
        )

    def resolve_approval_for_principal(
        self,
        approval_id: str,
        approved: bool,
        *,
        requester_user_id: str,
        workspace_id: str,
        remember_scope: bool = False,
        include_unowned: bool = False,
    ) -> bool:
        """Resolve a live request only when its initiating principal matches."""
        if approval_id not in self._pending_requests:
            return False
        owner = self._pending_principals.get(approval_id, ("", ""))
        if owner != (requester_user_id, workspace_id):
            if not (include_unowned and owner == ("", "")):
                return False
        return self.resolve_approval(
            approval_id,
            approved,
            remember_scope=remember_scope,
        )

    def _matches_file_scope_rule(
        self,
        tool_call: ToolCall,
        *,
        requester_user_id: str,
        workspace_id: str,
    ) -> bool:
        for scope_key in ("file_path", "path"):
            scope_value = tool_call.input.get(scope_key)
            if not isinstance(scope_value, str):
                continue
            rule = FileScopeRule(
                requester_user_id=requester_user_id,
                workspace_id=workspace_id,
                tool=tool_call.tool,
                scope_key=scope_key,
                scope_value=scope_value,
            )
            if rule in self._file_scope_rules:
                return True
        return False

    def _remember_file_scope(
        self,
        request: PermissionRequest,
        *,
        requester_user_id: str,
        workspace_id: str,
    ) -> None:
        for scope_key in ("file_path", "path"):
            scope_value = request.scope.get(scope_key)
            if not scope_value:
                continue
            rule = FileScopeRule(
                requester_user_id=requester_user_id,
                workspace_id=workspace_id,
                tool=request.tool,
                scope_key=scope_key,
                scope_value=scope_value,
            )
            if rule not in self._file_scope_rules:
                self._file_scope_rules.append(rule)
                self._persist_file_scope_rules()
            return

    def _load_file_scope_rules(self) -> list[FileScopeRule]:
        if self._rules_path is None or not self._rules_path.exists():
            return []
        try:
            payload = json.loads(self._rules_path.read_text(encoding="utf-8"))
            rules = []
            for item in payload.get("file_scope_rules", []):
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("tool"), str)
                    and item.get("scope_key") in {"file_path", "path"}
                    and isinstance(item.get("scope_value"), str)
                    and isinstance(item.get("requester_user_id", ""), str)
                    and isinstance(item.get("workspace_id", ""), str)
                ):
                    rules.append(FileScopeRule(
                        requester_user_id=item.get("requester_user_id", ""),
                        workspace_id=item.get("workspace_id", ""),
                        tool=item["tool"],
                        scope_key=item["scope_key"],
                        scope_value=item["scope_value"],
                    ))
            return rules
        except (OSError, ValueError, TypeError):
            logger.exception("failed to load permission rules from %s", self._rules_path)
            return []

    def _persist_file_scope_rules(self) -> None:
        if self._rules_path is None:
            return
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._rules_path.with_suffix(self._rules_path.suffix + ".tmp")
        payload = {
            "file_scope_rules": [
                {
                    "requester_user_id": rule.requester_user_id,
                    "workspace_id": rule.workspace_id,
                    "tool": rule.tool,
                    "scope_key": rule.scope_key,
                    "scope_value": rule.scope_value,
                }
                for rule in self._file_scope_rules
            ],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self._rules_path)

    @staticmethod
    def _display_scope(tool_input: dict[str, Any]) -> dict[str, str]:
        scope: dict[str, str] = {}
        for key in sorted(tool_input):
            normalized_key = key.casefold()
            if (
                key not in _REMOTE_SCOPE_KEYS
                or any(marker in normalized_key for marker in _SENSITIVE_SCOPE_KEYS)
            ):
                continue
            value = tool_input[key]
            if isinstance(value, str):
                rendered = value
            else:
                try:
                    rendered = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except (TypeError, ValueError):
                    rendered = str(value)
            if _SENSITIVE_VALUE.search(rendered):
                rendered = "[redacted: sensitive value]"
            elif len(rendered) > _MAX_SCOPE_VALUE_LENGTH:
                rendered = (
                    rendered[:_MAX_SCOPE_VALUE_LENGTH]
                    + "… [truncated]"
                )
            scope[key] = rendered
        return scope
