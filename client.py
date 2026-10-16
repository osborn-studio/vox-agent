#!/usr/bin/env python3
"""VoxLog Python client — press Right Option to record, release to paste.

Usage:
    python client.py

Requires: sounddevice, numpy, pynput, httpx
Server must be running: voxlog-server (or python -m uvicorn server.app:app --port 7890)
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import threading

import httpx
import numpy as np
import sounddevice as sd
from pynput import keyboard

# Config
SERVER_URL = os.getenv("VOXLOG_SERVER_URL", "http://127.0.0.1:7890").rstrip("/")
API_TOKEN = os.getenv("VOXLOG_API_TOKEN", "")
SAMPLE_RATE = 16000
CHANNELS = 1
MAX_SECONDS = 60

# State
recording = False
audio_frames: list[np.ndarray] = []
stream: sd.InputStream | None = None


def make_wav(pcm_data: bytes) -> bytes:
    """Wrap raw PCM int16 data in a WAV header."""
    data_size = len(pcm_data)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, data_size + 36)
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<H", header, 20, 1)  # PCM
    struct.pack_into("<H", header, 22, CHANNELS)
    struct.pack_into("<I", header, 24, SAMPLE_RATE)
    struct.pack_into("<I", header, 28, SAMPLE_RATE * CHANNELS * 2)
    struct.pack_into("<H", header, 32, CHANNELS * 2)
    struct.pack_into("<H", header, 34, 16)  # bits
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, data_size)
    return bytes(header) + pcm_data


def audio_callback(indata, frames, time_info, status):
    if recording:
        audio_frames.append(indata.copy())


def start_recording():
    global recording, audio_frames, stream
    if recording:
        return
    audio_frames = []
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=audio_callback,
    )
    stream.start()
    recording = True
    print("\r🔴 Recording... (release Right Option to stop)", end="", flush=True)


def stop_recording_and_process():
    global recording, stream
    if not recording:
        return
    recording = False
    if stream:
        stream.stop()
        stream.close()
        stream = None

    if not audio_frames:
        print("\r⚠️  No audio captured.                          ")
        return

    # Convert to WAV
    pcm = np.concatenate(audio_frames)
    duration = len(pcm) / SAMPLE_RATE
    print(f"\r⏳ Processing {duration:.1f}s audio...                    ", end="", flush=True)

    wav_data = make_wav(pcm.tobytes())

    # Send to server
    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
        resp = httpx.post(
            f"{SERVER_URL}/v1/voice",
            headers=headers,
            files={"audio": ("recording.wav", wav_data, "audio/wav")},
            data={"source": "python_client", "env": "home", "target_app": get_frontmost_app()},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()

        text = result["polished_text"]
        polished = result["polished"]
        provider = result["asr_provider"]
        latency = result["latency_ms"]

        # Copy to clipboard
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)

        # Paste via osascript (more reliable than simulating Cmd+V)
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
            check=True,
        )

        tag = "" if polished else " (raw)"
        print(f"\r✅ [{provider}] {latency}ms{tag}: {text[:80]}{'...' if len(text) > 80 else ''}          ")

    except httpx.HTTPStatusError as e:
        print(f"\r❌ Server error: {e.response.status_code} {e.response.text[:100]}          ")
    except Exception as e:
        print(f"\r❌ Error: {e}          ")

    print("🎤 Ready. Hold Right Option to record.", end="", flush=True)


def get_frontmost_app() -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def on_press(key):
    # Left Alt = Left Option on macOS (Windows 104-key keyboard compatible)
    if key == keyboard.Key.alt_l:
        start_recording()


def on_release(key):
    if key == keyboard.Key.alt_l and recording:
        # Process in background thread so keyboard listener isn't blocked
        threading.Thread(target=stop_recording_and_process, daemon=True).start()


def check_server():
    try:
        resp = httpx.get(f"{SERVER_URL}/health", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def main():
    print("=" * 50)
    print("  VoxLog — Your mouth has a save button now.")
    print("=" * 50)
    print()

    # Check server
    if not check_server():
        print("❌ VoxLog server not running on localhost:7890")
        print("   Start it first: cd ~/voxlog && source .venv/bin/activate && voxlog-server")
        sys.exit(1)

    print("✅ Server connected.")
    print("🎤 Ready. Hold Left Alt (Left Option) to record, release to paste.")
    print("   Press Ctrl+C to quit.")
    print()

    # Listen for hotkey
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            print("\n\nVoxLog stopped. Your voice history is saved in ~/.voxlog/history.db")


if __name__ == "__main__":
    main()
