"""Portable disk/NVRAM byte verification before booting a disposable Tart clone.

Tart rewrites VM config/identity during clone; launcher settings and shares need
separate validation. This receipt verifies actual disk and NVRAM bytes only.
"""

import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


PIN = "923c98d32e40ffadb6e6815a9722124b7a57bdf7d7763a708a2b28d1970831bd"
VERIFY_TIMEOUT = 600
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def regular(path):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Non-symlink regular image file required")
    return path


def hash_range(stream, length):
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        block = stream.read(min(4 * 1024 * 1024, remaining))
        if not block:
            raise ValueError("Truncated image bytes")
        digest.update(block)
        remaining -= len(block)
    return "sha256:" + digest.hexdigest()


def verify(manifest_path, vm_path, expected_sha256=PIN):
    manifest_path = regular(manifest_path)
    if manifest_path.stat().st_size > 512 * 1024:
        raise ValueError("Oversized manifest")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Pinned manifest hash mismatch")
    layers = json.loads(raw)["layers"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= 1024:
        raise ValueError("Invalid image layers")
    disks = [x for x in layers if x["mediaType"] == "application/vnd.cirruslabs.tart.disk.v2"]
    nvrams = [x for x in layers if x["mediaType"] == "application/vnd.cirruslabs.tart.nvram.v1"]
    if not disks or len(nvrams) != 1:
        raise ValueError("Unexpected disk/NVRAM layout")
    total = 0
    with regular(Path(vm_path) / "disk.img").open("rb") as stream:
        for layer in disks:
            annotations = layer["annotations"]
            size = annotations["org.cirruslabs.tart.uncompressed-size"]
            digest = annotations["org.cirruslabs.tart.uncompressed-content-digest"]
            if (not isinstance(size, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", size)
                    or int(size) > 512 * 1024 * 1024 or not DIGEST.fullmatch(digest)):
                raise ValueError("Invalid disk descriptor")
            if hash_range(stream, int(size)) != digest:
                raise ValueError("Disk layer hash mismatch")
            total += int(size)
        if stream.read(1):
            raise ValueError("Trailing disk bytes")
    nvram = nvrams[0]
    if (type(nvram["size"]) is not int or not 0 < nvram["size"] <= 1024 * 1024 * 1024
            or not DIGEST.fullmatch(nvram["digest"])):
        raise ValueError("Invalid NVRAM descriptor")
    with regular(Path(vm_path) / "nvram.bin").open("rb") as stream:
        if hash_range(stream, nvram["size"]) != nvram["digest"] or stream.read(1):
            raise ValueError("NVRAM hash/length mismatch")
    return {"status": "PINNED_IMAGE_BYTES_VERIFIED", "manifest_sha256": expected_sha256,
            "disk_layers_verified": len(disks), "uncompressed_disk_bytes": total,
            "nvram_sha256": nvram["digest"].removeprefix("sha256:")}


def verify_clone(manifest_path, vm_path):
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-E", "-s", str(Path(__file__).absolute()),
             str(manifest_path), str(vm_path)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=VERIFY_TIMEOUT, env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Clone byte verification unavailable or timed out") from None
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError("Clone byte verification failed")
    return json.loads(result.stdout)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(2)
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2]))))
