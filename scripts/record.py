#!/usr/bin/env python3
"""Simple recorder — run by Tauri, stop via SIGINT."""
import sounddevice as sd
import numpy as np
import struct
import os
import signal
import sys

RATE = 16000
WAV_PATH = os.path.expanduser("~/.voxlog2/recording.wav")
PID_PATH = WAV_PATH + ".pid"

# Write PID
os.makedirs(os.path.dirname(WAV_PATH), exist_ok=True)
with open(PID_PATH, "w") as f:
    f.write(str(os.getpid()))

frames = []

def callback(indata, frame_count, time_info, status):
    frames.append(indata.copy())

def save_and_exit(sig, frame):
    # Write WAV
    if frames:
        pcm = np.concatenate(frames)
        data = pcm.tobytes()
    else:
        data = b""

    header = bytearray(44)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, len(data) + 36)
    header[8:12] = b"WAVE"; header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<H", header, 20, 1)
    struct.pack_into("<H", header, 22, 1)
    struct.pack_into("<I", header, 24, RATE)
    struct.pack_into("<I", header, 28, RATE * 2)
    struct.pack_into("<H", header, 32, 2)
    struct.pack_into("<H", header, 34, 16)
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, len(data))
    with open(WAV_PATH, "wb") as f:
        f.write(bytes(header) + data)

    try: os.remove(PID_PATH)
    except: pass
    sys.exit(0)

signal.signal(signal.SIGINT, save_and_exit)
signal.signal(signal.SIGTERM, save_and_exit)

print(f"[record.py] Recording to {WAV_PATH} (PID {os.getpid()})")
with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", callback=callback):
    while True:
        sd.sleep(100)
