#!/usr/bin/env python3
"""Upload a verified encrypted Vox snapshot to a fixed private COS prefix.

Only PutObject/GetObject are used. No deletion, ACL changes or plaintext upload.
Provision SDK separately and supply credentials using systemd LoadCredentialEncrypted.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

BUCKET = "vox-team-backups-1377872589"
REGION = "ap-singapore"
MAX_BYTES = 512 * 1024 * 1024


def upload(path: Path, client) -> None:
    if not re.fullmatch(r"vox-\d{8}T\d{6}Z\.tar\.gpg", path.name) or path.is_symlink():
        raise ValueError("Unexpected archive path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 16 <= info.st_size <= MAX_BYTES:
            raise ValueError("Invalid archive size/type")
        first = source.read(1)[0]
        # GnuPG symmetric archives start with a tag-3 session-key packet.
        tag = first & 0x3F if first & 0x40 else (first >> 2) & 0x0F
        if not first & 0x80 or tag != 3:
            raise ValueError("Expected symmetric encrypted archive")
        source.seek(0)
        expected = hashlib.file_digest(source, "sha256").digest()
        source.seek(0)
        key = "vox-team/" + path.name
        client.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=source,
            EnableMD5=True,
            ContentType="application/octet-stream",
        )
    response = client.get_object(Bucket=BUCKET, Key=key)
    stream = response["Body"].get_raw_stream()
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > info.st_size:
                raise ValueError("COS verification size mismatch")
            digest.update(chunk)
    finally:
        stream.close()
    if size != info.st_size or digest.digest() != expected:
        raise ValueError("COS verification digest mismatch")


def main() -> int:
    try:
        from qcloud_cos import CosConfig, CosS3Client

        if len(sys.argv) > 1:
            path = Path(sys.argv[1])
        else:
            candidates = sorted(
                Path("/var/lib/vox-team-backups").glob("vox-????????T??????Z.tar.gpg")
            )
            if not candidates:
                raise ValueError("No verified local archive")
            path = candidates[-1]
        if time.time() - path.stat().st_mtime > 26 * 3600:
            raise ValueError("Local backup is stale")
        if path.parent != Path("/var/lib/vox-team-backups"):
            raise ValueError("Only the dedicated backup directory is allowed")
        credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "cos-backup"
        values = json.loads(credential.read_text())
        if set(values) != {"secret_id", "secret_key"} or not all(
            isinstance(v, str) and v for v in values.values()
        ):
            raise ValueError("Invalid credential")
        client = CosS3Client(
            CosConfig(
                Region=REGION,
                SecretId=values["secret_id"],
                SecretKey=values["secret_key"],
                Scheme="https",
                Timeout=30,
            )
        )
        upload(path, client)
    except Exception:
        # SDK exception strings can contain signed URLs or request headers.
        print("COS backup failed; local encrypted archive retained", file=sys.stderr)
        return 1
    print("COS encrypted backup upload and read-back digest verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
