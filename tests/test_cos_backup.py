"""Encrypted off-host backup guardrails; no real COS requests or credentials."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cos_backup", ROOT / "deploy/feishu-team/upload-cos.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Wire:
    def __init__(self):
        self.calls = []
        self.data = b""
        self.corrupt = False

    def put_object(self, **kw):
        self.calls.append(kw)
        self.data = kw["Body"].read()

    def get_object(self, **kw):
        import io

        self.calls.append(kw)
        data = b"corrupt" if self.corrupt else self.data

        class Body:
            def get_raw_stream(self):
                return io.BytesIO(data)

        return {"Body": Body()}


def test_encrypted_archive_uploaded_and_read_back(tmp_path):
    archive = tmp_path / "vox-20260928T120000Z.tar.gpg"
    archive.write_bytes(b"\x8c\x0d" + b"encrypted synthetic" * 10)
    client = Wire()
    module.upload(archive, client)
    assert client.calls[0]["Key"] == "vox-team/" + archive.name
    assert client.calls[0]["Bucket"] == "vox-team-backups-1377872589"
    assert client.calls[0]["EnableMD5"] is True
    assert len(client.calls) == 2


def test_mismatch_never_reports_success(tmp_path):
    archive = tmp_path / "vox-20260928T120000Z.tar.gpg"
    archive.write_bytes(b"\x8c\x0d" + b"encrypted synthetic")
    client = Wire()
    client.corrupt = True
    with pytest.raises(ValueError, match="verification"):
        module.upload(archive, client)
    assert archive.exists()


@pytest.mark.parametrize(
    "name,data", [("runtime.json", b"{}"), ("vox-20260928T120000Z.tar.gpg", b"plaintext")]
)
def test_plaintext_or_unexpected_name_rejected_without_request(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    client = Wire()
    with pytest.raises(ValueError):
        module.upload(path, client)
    assert client.calls == []


def test_symlink_rejected(tmp_path):
    source = tmp_path / "original"
    source.write_bytes(b"\x8c\x0d" + b"synthetic")
    link = tmp_path / "vox-20260928T120000Z.tar.gpg"
    link.symlink_to(source)
    client = Wire()
    with pytest.raises(ValueError):
        module.upload(link, client)
    assert client.calls == []
