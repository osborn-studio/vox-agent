#!/usr/bin/env python3
"""Qwen3-ASR WebSocket server for Type4Me.

Same WebSocket protocol as the SenseVoice server so the Swift client
(SenseVoiceWSClient) can connect without changes.

Protocol:
  - Client sends binary PCM16-LE audio frames (16kHz mono)
  - Client sends empty frame to signal end-of-audio
  - Server sends JSON: {"type": "transcript", "text": "...", "is_final": bool}
  - Server sends JSON: {"type": "completed"} when done
"""

import argparse
import asyncio
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

import re
import threading


class CancelToken:
    """Cooperative cancellation for thread-pool tasks."""
    __slots__ = ("_cancelled", "_lock")

    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

app = FastAPI()

_session = None
_model_path = None
_inference_lock = threading.Lock()  # Prevent concurrent Metal GPU access (thread-level)

SAMPLE_RATE = 16000
PARTIAL_INTERVAL_SEC = 0.6  # Run partial transcribe every N seconds of new audio (4-bit is fast)
MAX_PARTIAL_AUDIO_SEC = 45  # Only use last N seconds for partial (full audio for final)


def get_session():
    """Lazy-load the Qwen3-ASR Session with 4-bit quantization for speed."""
    global _session
    if _session is None:
        from mlx_qwen3_asr import Session
        import mlx.nn as nn
        _session = Session(_model_path)
        # Runtime 4-bit quantization: ~4x memory reduction, ~4x speed boost on M4
        nn.quantize(_session.model, group_size=64, bits=4)
        print("Model quantized to 4-bit", flush=True)
    return _session


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    sess = get_session()
    all_samples: list[int] = []
    partial_threshold = int(PARTIAL_INTERVAL_SEC * SAMPLE_RATE)
    max_partial_samples = MAX_PARTIAL_AUDIO_SEC * SAMPLE_RATE
    last_partial_at = 0  # sample count at last partial
    inflight_partial = None  # track running partial task
    cancel_token = CancelToken()  # shared cancellation for in-flight partials

    try:
        while True:
            data = await ws.receive_bytes()

            if len(data) == 0:
                # ── End of audio: final transcribe with punctuation ──
                cancel_token.cancel()  # signal any in-flight partial to bail out
                if inflight_partial and not inflight_partial.done():
                    inflight_partial.cancel()

                if all_samples:
                    text = await _transcribe(sess, all_samples, strip_punct=False)
                    if text:
                        await ws.send_json({
                            "type": "transcript",
                            "text": text,
                            "is_final": True,
                        })
                await ws.send_json({"type": "completed"})
                break

            # Accumulate PCM16 samples
            sample_count = len(data) // 2
            samples = list(struct.unpack(f"<{sample_count}h", data))
            all_samples.extend(samples)

            # Periodic partial: transcribe without punctuation
            new_audio = len(all_samples) - last_partial_at
            if new_audio >= partial_threshold:
                if inflight_partial is None or inflight_partial.done():
                    last_partial_at = len(all_samples)
                    # Cap partial audio to last N seconds to avoid O(total) re-processing
                    if len(all_samples) > max_partial_samples:
                        samples_snapshot = list(all_samples[-max_partial_samples:])
                    else:
                        samples_snapshot = list(all_samples)
                    inflight_partial = asyncio.ensure_future(
                        _send_partial(ws, sess, samples_snapshot, cancel_token)
                    )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


async def _send_partial(ws: WebSocket, sess, samples: list[int],
                        cancel_token: CancelToken | None = None):
    """Run transcribe on accumulated audio (no punctuation) and send as partial."""
    try:
        text = await _transcribe(sess, samples, strip_punct=True,
                                 cancel_token=cancel_token)
        if text:
            await ws.send_json({
                "type": "transcript",
                "text": text,
                "is_final": False,
            })
    except Exception:
        pass


# Punctuation characters to strip from partial results
_PUNCT_RE = re.compile('[，。！？、；：""''…,.!?;:]')


async def _transcribe(sess, samples_i16: list[int], strip_punct: bool = False,
                      cancel_token: CancelToken | None = None) -> str:
    """Run offline transcribe on audio samples."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _transcribe_sync, sess, samples_i16, strip_punct, cancel_token
    )


def _transcribe_sync(sess, samples_i16: list[int], strip_punct: bool = False,
                      cancel_token: CancelToken | None = None) -> str:
    """Synchronous transcription. Thread-safe via lock.

    Passes np.ndarray directly to Session.transcribe() (AudioInput supports
    np.ndarray natively), avoiding temp-file disk I/O entirely.
    """
    if cancel_token and cancel_token.is_cancelled:
        return ""
    with _inference_lock:
        if cancel_token and cancel_token.is_cancelled:
            return ""
        audio = np.array(samples_i16, dtype=np.float32) / 32768.0
        # A vocabulary list in generative context can be emitted as speech,
        # especially on silence and short chunks. Keep local ASR audio-only.
        result = sess.transcribe(audio)
        text = result.text.strip() if result and result.text else ""
        if strip_punct and text:
            text = _PUNCT_RE.sub("", text)
        return text


@app.post("/transcribe")
async def transcribe_http(request: Request):
    """HTTP endpoint for speculative transcription. Accepts raw PCM16-LE audio."""
    body = await request.body()
    if len(body) < 100:
        return {"text": ""}

    sample_count = len(body) // 2
    samples = list(struct.unpack(f"<{sample_count}h", body))

    text = await _transcribe(get_session(), samples, strip_punct=False)
    return {"text": text}


async def openai_compat_stream(request: Request):
    """OpenAI-compatible streaming endpoint for ASR.

    Accepts: {"model": "qwen3-asr-local", "stream": true, "messages": [...]}
    messages[0].content[0].input_audio.data = "data:audio/wav;base64,..."

    Returns: SSE stream of {"choices":[{"delta":{"content":"partial text"}}]}
    Same format as DashScope's qwen3-asr-flash cloud API.
    """
    from fastapi.responses import StreamingResponse

    body = await request.json()
    stream = body.get("stream", False)

    # Extract audio from messages
    audio_data = None
    messages = body.get("messages", [])
    for msg in messages:
        for content in msg.get("content", []):
            if isinstance(content, dict) and "input_audio" in content:
                audio_b64 = content["input_audio"].get("data", "")
                # Strip data URI prefix
                if ";base64," in audio_b64:
                    audio_b64 = audio_b64.split(";base64,")[1]
                import base64
                audio_data = base64.b64decode(audio_b64)

    if not audio_data or len(audio_data) < 100:
        return {"choices": [{"message": {"content": ""}}]}

    # Decode WAV to PCM samples
    if audio_data[:4] == b'RIFF':
        # Skip 44-byte WAV header
        pcm_bytes = audio_data[44:]
    else:
        pcm_bytes = audio_data

    sample_count = len(pcm_bytes) // 2
    samples = list(struct.unpack(f"<{sample_count}h", pcm_bytes))

    if not stream:
        text = await _transcribe(get_session(), samples, strip_punct=False)
        return {"choices": [{"message": {"content": text}}]}

    # Streaming: transcribe in chunks, emit SSE after each chunk
    async def generate():
        # Share the same audio-only inference path as HTTP and WebSocket.
        full_text = await _transcribe(get_session(), samples, strip_punct=False)

        if full_text:
            # Simulate streaming by yielding text in small chunks
            # This gives the client a progressive feel
            chars = list(full_text)
            chunk_size = max(1, len(chars) // 8)  # ~8 chunks
            for i in range(0, len(chars), chunk_size):
                partial = "".join(chars[i:i+chunk_size])
                data = json.dumps({
                    "choices": [{"delta": {"content": partial}}]
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"
                await asyncio.sleep(0.05)  # Small delay for visual effect

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": _model_path is not None, "llm_loaded": _llm is not None}


# --- LLM (Qwen3 via llama.cpp, optional) ---

_llm = None
_llm_lock = asyncio.Lock()
_llm_model_path = ""
_llm_disabled = False  # When True, refuse to load LLM (user clicked stop)


@app.post("/llm/unload")
async def llm_unload():
    """Unload the LLM model and prevent re-loading until /llm/load is called."""
    global _llm, _llm_disabled
    async with _llm_lock:
        _llm_disabled = True
        if _llm is not None:
            del _llm
            _llm = None
            import gc
            gc.collect()
            print("LLM unloaded and disabled.", flush=True)
            return {"status": "unloaded"}
        return {"status": "disabled"}


@app.post("/llm/load")
async def llm_load():
    """Re-enable LLM loading (after user clicks start)."""
    global _llm_disabled
    _llm_disabled = False
    print("LLM re-enabled.", flush=True)
    return {"status": "enabled"}


def _load_llm(model_path: str):
    global _llm
    if _llm is not None:
        return _llm
    if _llm_disabled:
        return None
    from llama_cpp import Llama
    print(f"Loading LLM from {model_path}...", flush=True)
    _llm = Llama(
        model_path=model_path,
        n_ctx=4096,
        n_gpu_layers=-1,
        verbose=False,
    )
    print("LLM loaded.", flush=True)
    return _llm


async def chat_completions(request: dict):
    if _llm_disabled:
        return {"error": "LLM disabled", "choices": [{"message": {"content": ""}}]}
    if _llm is None and not _llm_model_path:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "LLM not configured"}, status_code=503)

    messages = request.get("messages", [])
    temperature = request.get("temperature", 0.7)
    max_tokens = request.get("max_tokens", 1024)

    async with _llm_lock:
        llm = await asyncio.get_event_loop().run_in_executor(
            None, _load_llm, _llm_model_path
        )
    if llm is None:
        return {"error": "LLM disabled", "choices": [{"message": {"content": ""}}]}

    if messages and messages[-1].get("role") == "user":
        content = messages[-1]["content"]
        if not content.startswith("/no_think"):
            messages = messages.copy()
            messages[-1] = {**messages[-1], "content": f"/no_think\n{content}"}

    def _generate():
        import re
        # Share _inference_lock with ASR to prevent concurrent Metal GPU access
        with _inference_lock:
            result = llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        if result.get("choices"):
            text = result["choices"][0]["message"]["content"]
            text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()
            result["choices"][0]["message"]["content"] = text
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _generate)
    return result


@app.post("/v1/chat/completions")
async def dispatch_chat_completions(request: Request):
    """Keep audio compatibility and local text chat behind one unambiguous route."""
    from fastapi.responses import JSONResponse

    try:
        body = await request.json()
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("messages", []), list):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    messages = body.get("messages", [])
    if any(not isinstance(message, dict) for message in messages):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    has_audio = any(
        isinstance(message.get("content"), list)
        and any(isinstance(part, dict) and "input_audio" in part
                for part in message["content"])
        for message in messages
    )
    if "stream" in body and not isinstance(body["stream"], bool):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    if has_audio:
        import base64
        import binascii

        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, (str, list)):
                return JSONResponse({"error": "Invalid request"}, status_code=400)
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    return JSONResponse({"error": "Invalid request"}, status_code=400)
                if "input_audio" not in part:
                    continue
                audio = part["input_audio"]
                if not isinstance(audio, dict) or not isinstance(audio.get("data"), str):
                    return JSONResponse({"error": "Invalid request"}, status_code=400)
                encoded = audio["data"].split(";base64,", 1)[-1]
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    return JSONResponse({"error": "Invalid request"}, status_code=400)
                pcm = decoded[44:] if decoded[:4] == b"RIFF" else decoded
                if len(pcm) % 2:
                    return JSONResponse({"error": "Invalid request"}, status_code=400)
        return await openai_compat_stream(request)
    import math

    temperature = body.get("temperature", 0.7)
    max_tokens = body.get("max_tokens", 1024)
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not 0 <= temperature <= 2 or not math.isfinite(temperature)
            or isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
            or max_tokens <= 0):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    if not messages or any(
        not isinstance(message.get("content"), str)
        or not isinstance(message.get("role"), str) or not message["role"]
        for message in messages
    ):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    return await chat_completions(body)


# --- Main ---

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def runtime_self_check():
    """Exercise packaged native resources without opening models or a listener."""
    import mlx.core as mx
    from mlx_qwen3_asr import Session
    from llama_cpp import Llama

    result = mx.multiply(mx.array([2, 3]), mx.array([4, 5]), stream=mx.gpu)
    mx.eval(result)
    if result.tolist() != [8, 15] or not callable(Session.transcribe) or not callable(Llama):
        raise RuntimeError("runtime_self_check_failed")
    return {"status": "runtime_resources_passed", "model_loaded": False,
            "listener_started": False, "metal_result": result.tolist()}


def main():
    global _model_path, _llm_model_path

    # Prevent HF hub from trying to download/update models
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    parser = argparse.ArgumentParser(description="Qwen3-ASR Server")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--model-path", help="Path to Qwen3-ASR model directory")
    mode.add_argument("--runtime-self-check", action="store_true",
                      help="Check native runtime resources without model files or a listener")
    parser.add_argument("--port", type=int, default=0, help="0 = auto-assign")
    parser.add_argument("--hotwords-file", default="", help="Legacy compatibility argument; local generative ASR ignores vocabulary hints")
    parser.add_argument("--llm-model", default="", help="Path to GGUF LLM model for local chat completions")
    args = parser.parse_args()

    if args.runtime_self_check:
        try:
            print(json.dumps(runtime_self_check()), flush=True)
        except Exception:
            print(json.dumps({"status": "runtime_self_check_failed"}), flush=True)
            raise SystemExit(1)
        return

    _model_path = args.model_path

    if not Path(_model_path).exists():
        sys.exit(f"Model not found: {_model_path}")

    # Accept the legacy launcher argument without injecting the file into the
    # generative prompt. Other ASR providers retain their native hotword support.
    if args.hotwords_file:
        print("Local Qwen vocabulary prompt disabled to prevent transcript echo", flush=True)

    # Warm up: eagerly load model via Session
    print(f"Loading Qwen3-ASR model from {_model_path}...", flush=True)
    t0 = time.monotonic()
    sess = get_session()
    # Trigger model load with dummy transcription (np.ndarray, no temp file)
    dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
    sess.transcribe(dummy)
    elapsed = time.monotonic() - t0
    print(f"Model loaded in {elapsed:.1f}s", flush=True)

    # Configure LLM (lazy-loaded on first request)
    if args.llm_model and Path(args.llm_model).exists():
        _llm_model_path = args.llm_model
        print(f"LLM configured: {args.llm_model} (lazy load on first request)", flush=True)

    port = args.port if args.port != 0 else find_free_port()
    print(f"PORT:{port}", flush=True)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
