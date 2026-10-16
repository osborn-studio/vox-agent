"""Audio format detection, duration estimation, and WAV validation."""

from __future__ import annotations

import struct

import structlog

logger = structlog.get_logger()

_RIFF = b"RIFF"
_OGG = b"OggS"
_AMR = b"#!AMR"


def detect_format(data: bytes) -> str:
    if len(data) < 12:
        return "unknown"
    if data[:4] == _RIFF and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == _OGG:
        return "ogg"
    if data[:5] == _AMR:
        return "amr"
    return "unknown"


def validate_wav(data: bytes, max_seconds: int = 60) -> tuple[bool, str]:
    """Validate PCM WAV input and enforce the configured duration limit."""
    if len(data) < 44:
        return False, "too short to be a valid WAV file"
    if data[:4] != _RIFF or data[8:12] != b"WAVE":
        return False, "not a WAV file"

    audio_format = struct.unpack_from("<H", data, 20)[0]
    channels = struct.unpack_from("<H", data, 22)[0]
    sample_rate = struct.unpack_from("<I", data, 24)[0]
    bits_per_sample = struct.unpack_from("<H", data, 34)[0]
    if audio_format != 1:
        return False, f"unsupported audio format: {audio_format} (expected PCM)"

    data_size = len(data) - 44
    bytes_per_second = sample_rate * channels * (bits_per_sample // 8)
    if bytes_per_second == 0:
        return False, "invalid WAV header (zero bytes per second)"
    duration_seconds = data_size / bytes_per_second
    if duration_seconds > max_seconds:
        return False, f"audio too long: {duration_seconds:.1f}s (max {max_seconds}s)"

    logger.debug(
        "audio.validated",
        sample_rate=sample_rate,
        channels=channels,
        bits=bits_per_sample,
        duration=f"{duration_seconds:.1f}s",
    )
    return True, ""


def get_duration_seconds(data: bytes) -> float:
    """Estimate duration from a PCM WAV header and payload."""
    if len(data) < 44 or data[:4] != _RIFF:
        return 0.0
    channels = struct.unpack_from("<H", data, 22)[0]
    sample_rate = struct.unpack_from("<I", data, 24)[0]
    bits_per_sample = struct.unpack_from("<H", data, 34)[0]
    bytes_per_second = sample_rate * channels * (bits_per_sample // 8)
    if bytes_per_second == 0:
        return 0.0
    return (len(data) - 44) / bytes_per_second
