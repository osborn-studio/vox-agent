"""Tests for maintained runtime audio detection and WAV validation."""

import struct

from runtime.audio import detect_format, get_duration_seconds, validate_wav


def _make_wav(sample_rate=16000, channels=1, bits=16, seconds=1.0) -> bytes:
    """Create a minimal valid WAV file."""
    bytes_per_sample = bits // 8
    data_size = int(sample_rate * channels * bytes_per_sample * seconds)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, data_size + 36)
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)  # chunk size
    struct.pack_into("<H", header, 20, 1)  # PCM
    struct.pack_into("<H", header, 22, channels)
    struct.pack_into("<I", header, 24, sample_rate)
    struct.pack_into("<I", header, 28, sample_rate * channels * bytes_per_sample)
    struct.pack_into("<H", header, 32, channels * bytes_per_sample)
    struct.pack_into("<H", header, 34, bits)
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, data_size)
    return bytes(header) + b"\x00" * data_size


class TestDetectFormat:
    def test_wav(self):
        assert detect_format(_make_wav()) == "wav"

    def test_ogg(self):
        assert detect_format(b"OggS" + b"\x00" * 20) == "ogg"

    def test_amr(self):
        assert detect_format(b"#!AMR" + b"\x00" * 20) == "amr"

    def test_unknown(self):
        assert detect_format(b"\x00" * 20) == "unknown"

    def test_too_short(self):
        assert detect_format(b"\x00" * 5) == "unknown"


class TestValidateWav:
    def test_valid_wav(self):
        ok, err = validate_wav(_make_wav(seconds=5.0))
        assert ok is True
        assert err == ""

    def test_too_long(self):
        ok, err = validate_wav(_make_wav(seconds=65.0), max_seconds=60)
        assert ok is False
        assert "too long" in err

    def test_not_wav(self):
        ok, err = validate_wav(b"OggS" + b"\x00" * 50)
        assert ok is False
        assert "not a WAV" in err

    def test_too_short(self):
        ok, err = validate_wav(b"\x00" * 10)
        assert ok is False
        assert "too short" in err

    def test_exact_limit(self):
        ok, _ = validate_wav(_make_wav(seconds=60.0), max_seconds=60)
        assert ok is True


class TestGetDuration:
    def test_5_seconds(self):
        dur = get_duration_seconds(_make_wav(seconds=5.0))
        assert abs(dur - 5.0) < 0.1

    def test_not_wav(self):
        assert get_duration_seconds(b"not a wav") == 0.0

    def test_short_data(self):
        assert get_duration_seconds(b"") == 0.0
