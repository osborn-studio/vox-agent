"""Archive integrity and refusal checks using synthetic executable containers."""

import struct
import zlib

import pytest

from relink_qwen_soundfile import COOKIE, ENTRY, LIBRARY, MAGIC, archive, relink, repack
import relink_qwen_soundfile as tool


def container():
    # Minimal Mach-O layout with __LINKEDIT and SYMTAB load commands.
    prefix = bytearray(128)
    struct.pack_into("<IIIIIIII", prefix, 0, 0xFEEDFACF, 0x100000C, 0, 2, 2, 96, 0, 0)
    struct.pack_into("<II16sQQQQIIII", prefix, 32, 0x19, 72, b"__LINKEDIT", 0, 4096, 128, 0, 1, 1, 0, 0)
    struct.pack_into("<IIIIII", prefix, 104, 2, 24, 128, 0, 128, 0)
    data, toc = bytearray(), bytearray()
    for key, content in [(b"server", b"original frozen object"), (LIBRARY, b"original library")]:
        name = key + b"\0" * (16 - (ENTRY.size + len(key)) % 16)
        payload = zlib.compress(content)
        toc += ENTRY.pack(ENTRY.size + len(name), len(data), len(payload), len(content), 1, b"b") + name
        data += payload
    cookie = COOKIE.pack(MAGIC, len(data) + len(toc) + COOKIE.size, len(data), len(toc), 312, b"libpython3.12.dylib")
    return bytes(prefix + data + toc + cookie)


def test_replacement_preserves_unrelated_object_and_adjusts_bounds():
    original = container()
    before = archive(original)
    replacement = b"much larger modified library" * 91
    updated = repack(original, {LIBRARY: replacement})
    after = archive(updated)
    assert before[0:2] == after[0:2]
    assert before[2][0] == after[2][0]
    assert zlib.decompress(after[2][1][1]) == replacement
    assert struct.unpack_from("<Q", updated, 80)[0] == len(updated) - 128
    assert struct.unpack_from("<I", updated, 124)[0] == len(updated) - 128
    assert original == container()


def test_missing_member_refused():
    with pytest.raises(ValueError, match="missing"):
        repack(container(), {b"does-not-exist": b"replacement"})


def test_unsigned_unknown_helper_refused_without_touching_files(tmp_path):
    helper, library, output = (tmp_path / name for name in ["helper", "library", "output"])
    helper.write_bytes(container())
    library.write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="audited"):
        relink(helper, library, output)
    assert helper.read_bytes() == container()
    assert not output.exists()


def test_invalid_cookie_bounds_refused():
    raw = bytearray(container())
    struct.pack_into("!I", raw, len(raw) - COOKIE.size + 8, 0xFFFFFFFF)
    with pytest.raises(ValueError, match="bounds"):
        archive(raw)


@pytest.mark.parametrize("replacement", [b"\xcf\xfa\xed\xfe", container()])
def test_short_header_or_executable_cannot_masquerade_as_dylib(tmp_path, monkeypatch, replacement):
    helper, library, output = (tmp_path / name for name in ["helper", "library", "output"])
    original = container()
    helper.write_bytes(original)
    library.write_bytes(replacement)
    monkeypatch.setattr(tool, "AUDITED", {tool.sha(original)})
    with pytest.raises(ValueError, match="dylib"):
        relink(helper, library, output)
    assert helper.read_bytes() == original
    assert not output.exists()
