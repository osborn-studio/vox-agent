"""Tests for engine.webhooks — external service webhook processing."""

import pytest
from engine.webhooks import WebhookHandler, WebhookConfig, WebhookEvent


@pytest.fixture
def handler():
    h = WebhookHandler()
    h.register(WebhookConfig(
        id="gh-1", name="GitHub Repo", source="github", agent_id="claude-code",
    ))
    h.register(WebhookConfig(
        id="oc-1", name="OpenClaw", source="openclaw", agent_id="openclaw",
    ))
    h.register(WebhookConfig(
        id="custom-1", name="Custom", source="custom", agent_id="claude",
        prompt_template="Custom event: {event}",
    ))
    return h


class TestWebhookCRUD:
    def test_register(self, handler):
        assert len(handler.list_webhooks()) == 3

    def test_unregister(self, handler):
        assert handler.unregister("gh-1")
        assert len(handler.list_webhooks()) == 2

    def test_unregister_nonexistent(self, handler):
        assert not handler.unregister("nope")

    def test_get(self, handler):
        assert handler.get("gh-1") is not None
        assert handler.get("nope") is None


class TestGitHub:
    def test_push_event(self, handler):
        event = WebhookEvent(
            source="github", event_type="push",
            payload={
                "ref": "refs/heads/main",
                "commits": [{"message": "fix auth bug"}],
            },
        )
        result = handler.process("gh-1", event)
        assert result is not None
        assert "push" in result["title"].lower()
        assert "auth bug" in result["prompt"]

    def test_pr_event(self, handler):
        event = WebhookEvent(
            source="github", event_type="pull_request",
            payload={
                "action": "opened",
                "pull_request": {
                    "number": 42,
                    "title": "Add new feature",
                    "body": "This PR adds...",
                },
            },
        )
        result = handler.process("gh-1", event)
        assert result is not None
        assert "#42" in result["title"]

    def test_issue_event(self, handler):
        event = WebhookEvent(
            source="github", event_type="issues",
            payload={
                "action": "opened",
                "issue": {
                    "number": 99,
                    "title": "Bug report",
                    "body": "Steps to reproduce...",
                },
            },
        )
        result = handler.process("gh-1", event)
        assert result is not None
        assert "#99" in result["title"]


class TestOpenClaw:
    def test_completed(self, handler):
        event = WebhookEvent(
            source="openclaw", event_type="task_completed",
            payload={"status": "completed", "title": "Code review", "result": "All good"},
        )
        result = handler.process("oc-1", event)
        assert result is not None
        assert "completed" in result["title"].lower()

    def test_failed(self, handler):
        event = WebhookEvent(
            source="openclaw", event_type="task_failed",
            payload={"status": "failed", "title": "Deploy", "error": "timeout"},
        )
        result = handler.process("oc-1", event)
        assert result is not None
        assert "failed" in result["title"].lower()


class TestCustom:
    def test_custom_event(self, handler):
        event = WebhookEvent(
            source="custom", event_type="alert",
            payload={"message": "CPU usage high"},
        )
        result = handler.process("custom-1", event)
        assert result is not None
        assert "CPU usage" in result["prompt"]

    def test_disabled_webhook(self, handler):
        handler.get("custom-1").enabled = False
        event = WebhookEvent(source="custom", event_type="test", payload={})
        result = handler.process("custom-1", event)
        assert result is None
