"""Opt-in actual native runtime checks; never load model weights."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def invoke():
    python = os.environ.get("QWEN_RUNTIME_TEST_PYTHON")
    if not python:
        pytest.skip("Requires explicitly selected isolated Qwen runtime environment")
    server = Path(__file__).resolve().parents[1] / "macos/qwen3-asr-server/server.py"

    def run(*args):
        environment = os.environ.copy()
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        return subprocess.run([python, str(server), *args], capture_output=True,
                              text=True, timeout=30, env=environment)

    return run


def test_actual_native_resources_without_model_or_listener(invoke):
    result = invoke("--runtime-self-check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "runtime_resources_passed", "model_loaded": False,
        "listener_started": False, "metal_result": [8, 15],
    }


def test_requires_explicit_mode(invoke):
    result = invoke()
    assert result.returncode == 2


def test_probe_cannot_also_load_model(invoke):
    result = invoke("--runtime-self-check", "--model-path", "unused")
    assert result.returncode == 2


def test_normal_launch_still_refuses_missing_model(invoke, tmp_path):
    result = invoke("--model-path", str(tmp_path / "missing"))
    assert result.returncode == 1
    assert "Model not found" in result.stderr


def test_single_asgi_route_preserves_audio_and_text_modes():
    python = os.environ.get("QWEN_RUNTIME_TEST_PYTHON")
    if not python:
        pytest.skip("Requires explicitly selected isolated Qwen runtime environment")
    server = Path(__file__).resolve().parents[1] / "macos/qwen3-asr-server/server.py"
    code = r'''
import asyncio, base64, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("qwen_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert len([r for r in module.app.routes if getattr(r, "path", None) == "/v1/chat/completions"]) == 1
module.get_session = lambda: object()
async def transcribe(*args, **kwargs): return "synthetic audio"
module._transcribe = transcribe
async def request(body):
    sent = []
    raw = json.dumps(body).encode()
    async def receive(): return {"type": "http.request", "body": raw, "more_body": False}
    async def send(message): sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST",
             "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
             "query_string": b"", "headers": [(b"content-type", b"application/json")],
             "scheme": "http", "server": ("test", 80), "client": ("test", 1)}
    await module.app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload)
async def main():
    text = await request({"messages": [{"role": "user", "content": "synthetic text"}]})
    assert text == (503, {"error": "LLM not configured"}), text
    audio = await request({"messages": [{"content": [{"input_audio": {
        "data": base64.b64encode(b"\0" * 120).decode()}}]}]})
    assert audio == (200, {"choices": [{"message": {"content": "synthetic audio"}}]}), audio
    assert (await request([]))[0] == 400
    assert (await request({"messages": ["bad"]}))[0] == 400
asyncio.run(main())
'''
    result = subprocess.run([python, "-c", code, str(server)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
