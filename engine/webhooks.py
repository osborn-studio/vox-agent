"""Webhooks — allow external services to trigger agent tasks.

Supports incoming webhooks from:
- GitHub (push, PR, issues)
- OpenClaw task completion
- Custom integrations
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    id: str
    name: str
    source: str  # "github" | "openclaw" | "custom"
    agent_id: str = "claude-code"
    prompt_template: str = "Webhook received: {event}"
    secret: str = ""  # for signature verification
    enabled: bool = True


@dataclass
class WebhookEvent:
    source: str
    event_type: str
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class WebhookHandler:
    """Process incoming webhook events and create tasks."""

    def __init__(self) -> None:
        self._configs: dict[str, WebhookConfig] = {}

    def register(self, config: WebhookConfig) -> None:
        self._configs[config.id] = config
        logger.info("webhook registered: %s (%s)", config.name, config.source)

    def unregister(self, webhook_id: str) -> bool:
        return self._configs.pop(webhook_id, None) is not None

    def list_webhooks(self) -> list[WebhookConfig]:
        return list(self._configs.values())

    def get(self, webhook_id: str) -> WebhookConfig | None:
        return self._configs.get(webhook_id)

    def process(self, webhook_id: str, event: WebhookEvent) -> dict | None:
        """Process a webhook event. Returns task creation params or None."""
        config = self._configs.get(webhook_id)
        if not config or not config.enabled:
            return None

        # Verify signature if secret is set
        if config.secret:
            if not self._verify_signature(config, event):
                logger.warning("webhook %s: signature verification failed", webhook_id)
                return None

        # Parse event based on source
        parsed = self._parse_event(config, event)
        if not parsed:
            return None

        return {
            "agent_id": config.agent_id,
            "title": parsed["title"],
            "prompt": parsed["prompt"],
            "source": f"webhook:{config.source}",
        }

    def _verify_signature(self, config: WebhookConfig, event: WebhookEvent) -> bool:
        """Verify webhook signature (GitHub-style HMAC-SHA256)."""
        sig_header = event.headers.get("x-hub-signature-256", "")
        if not sig_header.startswith("sha256="):
            return False

        body = json.dumps(event.payload).encode()
        expected = "sha256=" + hmac.new(
            config.secret.encode(), body, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(sig_header, expected)

    def _parse_event(self, config: WebhookConfig, event: WebhookEvent) -> dict | None:
        """Parse webhook payload into task params."""
        if config.source == "github":
            return self._parse_github(event)
        elif config.source == "openclaw":
            return self._parse_openclaw(event)
        else:
            return self._parse_custom(config, event)

    def _parse_github(self, event: WebhookEvent) -> dict | None:
        payload = event.payload
        event_type = event.event_type

        if event_type == "push":
            commits = payload.get("commits", [])
            branch = payload.get("ref", "").split("/")[-1]
            msg = commits[0]["message"] if commits else "push"
            return {
                "title": f"GitHub push: {branch} — {msg[:40]}",
                "prompt": f"A push was made to {branch}. {len(commits)} commits.\n\nLatest: {msg}\n\nReview the changes.",
            }
        elif event_type == "pull_request":
            pr = payload.get("pull_request", {})
            action = payload.get("action", "")
            return {
                "title": f"PR #{pr.get('number', '')}: {pr.get('title', '')[:40]}",
                "prompt": f"PR {action}: #{pr.get('number')} {pr.get('title')}\n\n{pr.get('body', '')[:500]}",
            }
        elif event_type == "issues":
            issue = payload.get("issue", {})
            action = payload.get("action", "")
            return {
                "title": f"Issue #{issue.get('number', '')}: {issue.get('title', '')[:40]}",
                "prompt": f"Issue {action}: #{issue.get('number')} {issue.get('title')}\n\n{issue.get('body', '')[:500]}",
            }

        return None

    def _parse_openclaw(self, event: WebhookEvent) -> dict | None:
        payload = event.payload
        status = payload.get("status", "")
        task_title = payload.get("title", "OpenClaw task")

        if status == "completed":
            return {
                "title": f"OpenClaw completed: {task_title[:40]}",
                "prompt": f"OpenClaw task completed: {task_title}\n\nResult: {payload.get('result', 'no details')}",
            }
        elif status == "failed":
            return {
                "title": f"OpenClaw failed: {task_title[:40]}",
                "prompt": f"OpenClaw task failed: {task_title}\n\nError: {payload.get('error', 'unknown')}",
            }

        return None

    def _parse_custom(self, config: WebhookConfig, event: WebhookEvent) -> dict | None:
        summary = json.dumps(event.payload, ensure_ascii=False)[:500]
        prompt = config.prompt_template.replace("{event}", summary)
        return {
            "title": f"Webhook: {config.name}",
            "prompt": prompt,
        }
