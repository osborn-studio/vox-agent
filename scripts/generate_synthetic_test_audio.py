#!/usr/bin/env python3
"""Generate a deterministic non-speech PCM fixture; no recording or voice model.

The integer triangle wave is deliberately simple and portable. This checks audio
transport/decoding, not speech recognition accuracy. Licensed under the repo MIT.
"""

import argparse
import struct
import wave
from pathlib import Path


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    # Six seconds, 16 kHz, mono signed PCM16; period 80 = 200 Hz.
    pcm = b"".join(
        struct.pack("<h", (abs((i % 80) - 40) - 20) * 200)
        for i in range(96000)
    )
    with wave.open(str(output / "test_audio.wav"), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    # NPY v1.0: ASCII dictionary padded so header + prefix ends at 64 bytes.
    header = b"{'descr': '<i2', 'fortran_order': False, 'shape': (96000,), }"
    header += b" " * ((64 - ((10 + len(header) + 1) % 64)) % 64) + b"\n"
    (output / "test_audio.npy").write_bytes(
        b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + pcm
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output)
