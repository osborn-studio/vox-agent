"""Snapshot ordinary external media assets; no authenticity or signing claim."""
import hashlib
import os
import json
from pathlib import Path


def assets_inventory(root: Path):
    entries = {}

    def fail(error):
        raise error

    if root.is_symlink() or not root.is_dir():
        raise ValueError('Ordinary OfflineAssets directory required')
    for parent, directories, files in os.walk(root, followlinks=False, onerror=fail):
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError('Offline assets must not contain links')
            if path.is_file():
                with path.open('rb') as incoming:
                    entries[path.relative_to(root).as_posix()] = hashlib.file_digest(incoming, 'sha256').hexdigest()
            elif not path.is_dir():
                raise ValueError('Unsupported offline asset')
    if not entries:
        raise ValueError('Empty offline assets')
    return entries




def verify_runtime_preparation(app: Path, model_identity):
    record = json.loads((app / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json').read_text())
    if not isinstance(record, dict) or record.get('status') != 'PREPARED_REQUIRES_SIGNING' or record.get('model_identity') != model_identity:
        raise ValueError('Complete runtime preparation record required')
    with (app / 'Contents/MacOS/qwen3-asr-server').open('rb') as helper:
        digest = hashlib.file_digest(helper, 'sha256').hexdigest()
    if record.get('helper_sha256') != digest or record.get('notice_file_sha256') != assets_inventory(app / 'Contents/Resources/QwenRuntimeLicenses'):
        raise ValueError('Runtime preparation contents changed')
