#!/usr/bin/env python3
"""Replace libsndfile in a COPY of one of the three audited arm64 helpers.

Uses only Python's standard library and macOS codesign. Does not modify an app,
installed executable, original helper, or release asset. Requires macOS arm64.
"""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE = struct.Struct("!8sIIII64s")
ENTRY = struct.Struct("!IIIIBc")
LIBRARY = b"_soundfile_data/libsndfile_arm64.dylib"
AUDITED = {
    "97cbef3d95900398be96987b0f775d787527e178a2fd2a8015d505af50ed1dfb",
    "b2ca797fe167814b03193b3a2f9d8f91081aff255c90c192b2d14d2fd97978b2",
    "f09042e2f673472a50c5d21b6602e72b3efd24b6262a4a0e032204c53402a1cd",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(raw):
    raw = bytes(raw)
    pos = raw.rfind(MAGIC)
    if pos < 0 or pos + COOKIE.size > len(raw):
        raise ValueError("missing CArchive cookie")
    magic, total, offset, size, python, pylib = COOKIE.unpack_from(raw, pos)
    start = pos + COOKIE.size - total
    if start < 0 or offset + size != total - COOKIE.size:
        raise ValueError("invalid CArchive bounds")
    toc = raw[start + offset:start + offset + size]
    cursor = 0
    entries = []
    names = set()
    while cursor < len(toc):
        length, off, packed, unpacked, comp, kind = ENTRY.unpack_from(toc, cursor)
        if length < ENTRY.size + 1 or cursor + length > len(toc):
            raise ValueError("invalid TOC entry")
        name = toc[cursor + ENTRY.size:cursor + length]
        key = name.rstrip(b"\0")
        if key in names or off + packed > offset or comp not in (0, 1):
            raise ValueError("invalid member")
        names.add(key)
        payload = raw[start + off:start + off + packed]
        decoded = zlib.decompress(payload) if comp else payload
        if len(decoded) != unpacked:
            raise ValueError("invalid unpacked length")
        entries.append((name, payload, unpacked, comp, kind))
        cursor += length
    return start, (magic, python, pylib), entries


def fix_macho(raw):
    # Same size adjustment described by PyInstaller's fix_exe_for_code_signing:
    # the archive extends the final string table and __LINKEDIT segment.
    data = bytearray(raw)
    if data[:4] != b"\xcf\xfa\xed\xfe" or struct.unpack_from("<I", data, 4)[0] != 0x100000C:
        raise ValueError("expected thin little-endian arm64 Mach-O")
    ncmds, cmdbytes = struct.unpack_from("<II", data, 16)
    cursor = 32
    segments = symbols = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, cursor)
        if size < 8 or cursor + size > 32 + cmdbytes:
            raise ValueError("invalid Mach-O load command")
        if cmd == 0x1D:
            raise ValueError("remove signature before adjusting headers")
        if cmd == 0x19 and data[cursor + 8:cursor + 24].rstrip(b"\0") == b"__LINKEDIT":
            fileoff = struct.unpack_from("<Q", data, cursor + 40)[0]
            filesize = len(data) - fileoff
            struct.pack_into("<Q", data, cursor + 32, (filesize + 16383) // 16384 * 16384)
            struct.pack_into("<Q", data, cursor + 48, filesize)
            segments += 1
        if cmd == 2:
            stroff = struct.unpack_from("<I", data, cursor + 16)[0]
            struct.pack_into("<I", data, cursor + 20, len(data) - stroff)
            symbols += 1
        cursor += size
    if segments != 1 or symbols != 1:
        raise ValueError("unexpected Mach-O layout")
    return data


def repack(raw, replacements):
    start, (magic, python, pylib), entries = archive(raw)
    data, toc = bytearray(), bytearray()
    found = set()
    for name, payload, unpacked, comp, kind in entries:
        key = name.rstrip(b"\0")
        if key in replacements:
            content = replacements[key]
            unpacked = len(content)
            payload = zlib.compress(content, 9) if comp else content
            found.add(key)
        toc += ENTRY.pack(ENTRY.size + len(name), len(data), len(payload), unpacked, comp, kind) + name
        data += payload
    if found != set(replacements):
        raise ValueError("replacement member missing")
    total = len(data) + len(toc) + COOKIE.size
    return fix_macho(raw[:start] + data + toc + COOKIE.pack(magic, total, len(data), len(toc), python, pylib))


def relink(helper, library, output):
    original = helper.read_bytes()
    if sha(original) not in AUDITED:
        raise ValueError("helper hash is not in the audited set")
    if output.exists() or output.is_symlink():
        raise ValueError("output must not exist")
    replacement = library.read_bytes()
    if (len(replacement) < 32 or replacement[:4] != b"\xcf\xfa\xed\xfe"
            or struct.unpack_from("<I", replacement, 4)[0] != 0x100000C
            or struct.unpack_from("<I", replacement, 12)[0] != 6):
        raise ValueError("replacement must be a thin arm64 dylib")
    with tempfile.TemporaryDirectory(prefix="osvox-relink-", dir=output.parent) as temp:
        staged = Path(temp) / "qwen3-asr-server"
        staged.write_bytes(original)
        staged.chmod(0o755)
        subprocess.run(["/usr/bin/codesign", "--remove-signature", str(staged)], check=True)
        unsigned = staged.read_bytes()
        staged.write_bytes(repack(unsigned, {LIBRARY: replacement}))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staged)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(staged)], check=True)
        before = archive(original)[2]
        after = archive(staged.read_bytes())[2]
        # Every other member's compressed payload and metadata stays identical.
        if len(before) != len(after) or any(a != b for a, b in zip(before, after) if a[0].rstrip(b"\0") != LIBRARY):
            raise ValueError("unrelated member changed")
        with output.open("xb") as destination, staged.open("rb") as source:
            shutil.copyfileobj(source, destination)
        output.chmod(0o755)
    return {"input_sha256": sha(original), "library_sha256": sha(replacement),
            "output_sha256": sha(output.read_bytes()), "preserved_members": len(before) - 1,
            "signature": "ad-hoc; strict verification passed", "original_unchanged": helper.read_bytes() == original}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(relink(args.helper, args.library, args.output), indent=2))
