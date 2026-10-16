"""Default-CI ASGI checks using synthetic PCM and fake inference, never models."""
import base64
import importlib.util
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def server(monkeypatch):
    # NumPy belongs to the optional ASR runtime, not root CI dependencies.
    # Only import it as an unused placeholder: real inference is forbidden below.
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
        path = Path(__file__).resolve().parents[1] / "macos/qwen3-asr-server/server.py"
        spec = importlib.util.spec_from_file_location("qwen_boundary", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    def forbidden(*args, **kwargs):
        raise AssertionError("Request must not initialize a real model")

    monkeypatch.setattr(module, "get_session", forbidden)
    monkeypatch.setattr(module, "_load_llm", forbidden)
    monkeypatch.setattr(module, "_llm_model_path", "synthetic")
    return module


def audio_request(size=120, **options):
    return {"messages": [{"content": [{"input_audio": {
        "data": base64.b64encode(bytes(size)).decode()}}]}], **options}


@pytest.mark.parametrize("body", [
    {"messages": [{"content": [{"input_audio": None}]}]},
    {"messages": [{"content": [{"input_audio": {"data": 123}}]}]},
    {"messages": [{"content": [{"input_audio": {"data": "!!bad"}}]}]},
    audio_request(121),
    {"messages": [{"role": "user", "content": {}}]},
    {"messages": [{"role": "user"}]},
    {"messages": [{"content": [{"input_audio": {"data": ""}}]},
                  {"content": None}]},
    {"messages": [], "stream": "false"},
    {},
    {"messages": []},
    {"messages": [{"content": "text"}]},
    {"messages": [{"role": True, "content": "text"}]},
    {"messages": [{"role": "", "content": "text"}]},
])
def test_invalid_input_returns_400_before_inference(server, body):
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid request"}


@pytest.mark.parametrize("field,value", [
    ("temperature", True), ("temperature", "warm"),
    ("temperature", -1), ("temperature", 3),
    ("temperature", 10 ** 1000),
    ("max_tokens", True), ("max_tokens", "10"),
    ("max_tokens", 0), ("max_tokens", -1), ("max_tokens", 1.5),
])
def test_invalid_numeric_parameters_never_load_llm(server, field, value):
    body = {"messages": [{"role": "user", "content": "text"}], field: value}
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400


@pytest.mark.parametrize("raw", [
    b'{"messages":', b"[" * 2000 + b"]" * 2000,
    b'{"messages":[{"role":"user","content":"text"}],"temperature":NaN}',
    b'{"messages":[{"role":"user","content":"text"}],"temperature":Infinity}',
])
def test_invalid_json_or_nonfinite_temperature_is_generic_400(server, raw):
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", content=raw,
                               headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid request"}


@pytest.mark.parametrize("stream", [False, True])
def test_audio_success_and_sse_use_fake_transcription(server, monkeypatch, stream):
    calls = []
    monkeypatch.setattr(server, "get_session", lambda: "fake session")

    async def transcribe(session, samples, strip_punct):
        calls.append((session, samples, strip_punct))
        return "synthetic audio"

    monkeypatch.setattr(server, "_transcribe", transcribe)
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json=audio_request(stream=stream))
    assert response.status_code == 200
    assert calls == [("fake session", [0] * 60, False)]
    if stream:
        import json
        chunks = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        assert chunks[-1] == "[DONE]"
        assert "".join(json.loads(c)["choices"][0]["delta"]["content"]
                       for c in chunks[:-1]) == "synthetic audio"
        assert response.headers["content-type"].startswith("text/event-stream")
    else:
        assert response.json() == {"choices": [{"message": {"content": "synthetic audio"}}]}


def test_configured_text_chat_succeeds_without_real_llm(server, monkeypatch):
    calls = []

    class FakeLLM:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "<think>hidden</think> reply"}}]}

    monkeypatch.setattr(server, "_load_llm", lambda path: FakeLLM())
    body = {"messages": [{"role": "user", "content": "text"}],
            "temperature": 0, "max_tokens": 1}
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "reply"
    assert calls == [{"messages": [{"role": "user", "content": "/no_think\ntext"}],
                      "temperature": 0, "max_tokens": 1}]
    assert body["messages"][0]["content"] == "text"


def test_disabled_llm_preserves_existing_response_without_loading(server, monkeypatch):
    monkeypatch.setattr(server, "_llm_disabled", True)
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "text"}]})
    assert response.status_code == 200
    assert response.json() == {"error": "LLM disabled",
                               "choices": [{"message": {"content": ""}}]}


def test_unconfigured_llm_returns_real_503_and_only_one_route(server, monkeypatch):
    monkeypatch.setattr(server, "_llm_model_path", "")
    assert len([r for r in server.app.routes
                if getattr(r, "path", None) == "/v1/chat/completions"]) == 1
    with TestClient(server.app) as client:
        response = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "text"}]})
    assert response.status_code == 503
    assert response.json() == {"error": "LLM not configured"}
