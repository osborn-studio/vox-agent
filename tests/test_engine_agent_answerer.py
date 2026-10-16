"""Tests for engine.agent_answerer — the pluggable answer seam."""

import os

import pytest

from engine.agent_answerer import LlmAnswerer, MockAnswerer, get_answerer


def _node(nid, title, body, ntype="DECISION"):
    return {"id": nid, "title": title, "body": body, "type": ntype}


class TestMockAnswerer:
    def test_name(self):
        assert MockAnswerer().name == "mock"

    def test_deterministic(self):
        nodes = [_node("mem_1", "Launch in Japan", "We will launch in July.")]
        a = MockAnswerer().answer("japan launch", nodes)
        b = MockAnswerer().answer("japan launch", nodes)
        assert a == b
        assert isinstance(a, str) and a

    def test_grounded_in_context(self):
        nodes = [_node("mem_1", "Launch in Japan", "We will launch in July.")]
        answer = MockAnswerer().answer("when do we launch", nodes)
        # references the actual matched memory (title or body), not a canned stub
        assert "Launch in Japan" in answer or "July" in answer

    def test_no_context(self):
        answer = MockAnswerer().answer("anything", [])
        assert "no" in answer.lower() or "0" in answer  # signals empty, no crash


class TestRegistry:
    def test_default_is_mock(self, monkeypatch):
        monkeypatch.delenv("VOXAGENT_ANSWERER", raising=False)
        assert get_answerer().name == "mock"

    def test_env_selection(self, monkeypatch):
        monkeypatch.setenv("VOXAGENT_ANSWERER", "mock")
        assert get_answerer().name == "mock"

    def test_llm_is_registered(self, monkeypatch):
        monkeypatch.setenv("VOXAGENT_ANSWERER", "llm")
        assert get_answerer().name == "llm"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown answerer"):
            get_answerer("does-not-exist")


class TestLlmAnswerer:
    def test_name(self):
        assert LlmAnswerer(complete=lambda m: "x").name == "llm"

    def test_no_context_short_circuits_without_calling_llm(self):
        calls = []
        answerer = LlmAnswerer(complete=lambda m: calls.append(m) or "unused")
        answer = answerer.answer("anything", [])
        assert "No memory found" in answer
        assert calls == []  # no LLM call when there's no context

    def test_answer_is_grounded_and_bounded(self):
        captured = {}

        def fake_complete(messages):
            captured["messages"] = messages
            return "  The launch is in July [1].  "

        long_body = "J" * 5000
        nodes = [_node("mem_1", "Launch in Japan", long_body)]
        answer = LlmAnswerer(complete=fake_complete).answer("when do we launch", nodes)
        assert answer == "The launch is in July [1]."  # stripped
        user_msg = captured["messages"][-1]["content"]
        assert "Launch in Japan" in user_msg  # grounded in the node
        assert "when do we launch" in user_msg  # includes the query
        # bounded: the 5000-char body is truncated in the prompt
        assert long_body not in user_msg
        assert user_msg.count("J") < 1000

    def test_caps_number_of_nodes(self):
        captured = {}

        def fake_complete(messages):
            captured["messages"] = messages
            return "ok"

        nodes = [_node(f"mem_{i}", f"Node {i}", "body") for i in range(20)]
        LlmAnswerer(complete=fake_complete).answer("q", nodes)
        user_msg = captured["messages"][-1]["content"]
        assert "Node 0" in user_msg
        assert "Node 19" not in user_msg  # capped to _LLM_MAX_NODES

    def test_missing_config_raises_runtime_error(self, monkeypatch):
        for var in ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL"):
            monkeypatch.delenv(var, raising=False)
        # default (no injected complete) → the http path checks config first
        answerer = LlmAnswerer()
        with pytest.raises(RuntimeError, match="not configured"):
            answerer.answer("q", [_node("mem_1", "T", "B")])

    def test_upstream_http_error_normalized_to_runtime_error(self, monkeypatch):
        import httpx

        monkeypatch.setenv("VOXAGENT_LLM_BASE_URL", "http://llm.test/v1")
        monkeypatch.setenv("VOXAGENT_LLM_API_KEY", "sk-secret-should-not-leak")
        monkeypatch.setenv("VOXAGENT_LLM_MODEL", "m")

        class _FakeResponse:
            status_code = 401

            def raise_for_status(self):
                raise httpx.HTTPStatusError("unauthorized", request=None, response=self)

            def json(self):  # pragma: no cover - not reached
                return {}

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return _FakeResponse()

        monkeypatch.setattr(httpx, "Client", _FakeClient)
        answerer = LlmAnswerer()  # real _http_complete path
        with pytest.raises(RuntimeError, match="status 401") as exc:
            answerer.answer("q", [_node("mem_1", "T", "B")])
        assert "sk-secret" not in str(exc.value)  # key never leaks into the error


_LLM_CONFIGURED = all(
    os.environ.get(v)
    for v in ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL")
)


@pytest.mark.skipif(not _LLM_CONFIGURED, reason="VOXAGENT_LLM_* not configured")
class TestLlmAnswererLive:
    def test_real_completion(self):
        nodes = [_node("mem_1", "Launch in Japan", "We will launch in July 2026.")]
        answer = LlmAnswerer().answer("when is the japan launch", nodes)
        assert isinstance(answer, str) and answer.strip()
