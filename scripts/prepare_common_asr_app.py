"""Prepare a new common App with an explicit runtime helper before signing.

Never executes helper/App, signs, installs, downloads or deletes original sources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unicodedata

from build_split_model_media import private_parent
from split_media_manifest import assets_inventory
from verify_app_equivalence import inventory, verify as verify_equivalence
from verify_small_app_bundle import verify as verify_small_app


def notice_files(root: Path):
    if root != root.resolve() or root.is_symlink() or not root.is_dir():
        raise ValueError('Ordinary physical runtime notices directory required')
    if root.lstat().st_mode & 0o022:
        raise ValueError('Runtime notices root is writable by others')
    files = []
    targets = set()

    def fail(error):
        raise error

    for parent, directories, names in os.walk(root, followlinks=False, onerror=fail):
        for name in directories + names:
            path = Path(parent) / name
            info = path.lstat()
            if info.st_mode & 0o022 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsafe runtime notice entry')
            if stat.S_ISREG(info.st_mode):
                if not any(word in name.lower() for word in ['license', 'copying', 'notice']):
                    raise ValueError('Only runtime license/notice files are accepted')
                if info.st_mode & 0o111 or path.suffix.lower() not in {'', '.txt', '.md', '.html'} or info.st_size > 2 * 1024 * 1024:
                    raise ValueError('Notice files must be bounded non-executable text')
                raw = path.read_bytes()
                if b'\x00' in raw:
                    raise ValueError('Binary notice files are not accepted')
                raw.decode('utf-8')
                target = unicodedata.normalize('NFC', path.relative_to(root).as_posix()).casefold()
                if target in targets:
                    raise ValueError('Notice paths collide on a case-insensitive volume')
                targets.add(target)
                files.append(path)
    if not files:
        raise ValueError('Runtime notices are required')
    return files


def prepare(source: Path, helper: Path, notices: Path, output: Path):
    private_parent(output.parent)
    if os.path.lexists(output) or not output.name.endswith('.app'):
        raise ValueError('Output must be a new App path')
    for original in [source, helper, notices]:
        if original != original.resolve() or output.is_relative_to(original):
            raise ValueError('Use physical sources and independent output')
    verify_small_app(source)
    snapshot = inventory(source)
    if any(item[0] != 'link' and item[1] & 0o022 for item in snapshot.values()):
        raise ValueError('Source App is group/world writable')
    expected = json.loads((source / 'Contents/Resources/QWEN_MODEL_CATALOG.json').read_text())
    if not isinstance(expected, dict) or set(expected) != {'model_id', 'revision', 'digest'} or any(not isinstance(v, str) or not v for v in expected.values()):
        raise ValueError('Source must carry build-generated model identity')
    info = helper.lstat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111 or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Use an owned ordinary non-writable-by-others executable helper')
    licenses = notice_files(notices)
    target = output / 'Contents/MacOS/qwen3-asr-server'
    if os.path.lexists(source / 'Contents/MacOS/qwen3-asr-server') or os.path.lexists(source / 'Contents/Resources/QwenRuntimeLicenses') or os.path.lexists(source / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json'):
        raise ValueError('Source already has runtime assets; do not overwrite')
    try:
        subprocess.run(['/usr/bin/ditto', '--rsrc', '--extattr', '--acl', str(source), str(output)],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        verify_equivalence(source, output)
        shutil.copy2(helper, target, follow_symlinks=False)
        target.chmod(0o755)
        with helper.open('rb') as original, target.open('rb') as copied:
            digest = hashlib.file_digest(original, 'sha256').hexdigest()
            if hashlib.file_digest(copied, 'sha256').hexdigest() != digest:
                raise ValueError('Runtime helper copy changed')
        notices_root = output / 'Contents/Resources/QwenRuntimeLicenses'
        notices_root.mkdir(mode=0o755)
        notices_root.chmod(0o755)
        for path in licenses:
            destination = notices_root / path.relative_to(notices)
            folder = notices_root
            for component in path.relative_to(notices).parts[:-1]:
                folder = folder / component
                folder.mkdir(mode=0o755, exist_ok=True)
                folder.chmod(0o755)
            with path.open('rb') as incoming, destination.open('xb') as copied:
                shutil.copyfileobj(incoming, copied)
            destination.chmod(0o644)
        if assets_inventory(notices_root) != assets_inventory(notices):
            raise ValueError('Notice copy changed')
        verify_small_app(output)
        if inventory(source) != snapshot:
            raise ValueError('Original App changed')
        completed = inventory(output)
        if any(item[0] != 'link' and item[1] & 0o022 for item in completed.values()):
            raise ValueError('Prepared App additions are writable by others')
        record = {'status': 'PREPARED_REQUIRES_SIGNING', 'helper_sha256': digest,
                  'model_identity': expected, 'notice_file_sha256': assets_inventory(notices_root)}
        record_path = output / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json'
        with record_path.open('x') as marker:
            json.dump(record, marker, sort_keys=True, indent=2)
            marker.flush()
            os.fsync(marker.fileno())
        record_path.chmod(0o644)
        return {'common_app': str(output), 'helper_sha256': digest, 'model_identity': expected,
                'requires_signing': True, 'runtime_executed': False, 'notice_completeness_verified': False}
    except BaseException:
        print('Incomplete common App retained: ' + str(output), file=sys.stderr)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app', required=True, type=Path)
    parser.add_argument('--runtime-helper', required=True, type=Path)
    parser.add_argument('--runtime-notices', required=True, type=Path)
    parser.add_argument('--output-app', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source_app, args.runtime_helper, args.runtime_notices, args.output_app), sort_keys=True))
