"""Render and read-only verify a split staging DMG; never sign, install or upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from build_split_model_media import private_parent
from split_media_manifest import assets_inventory
from verify_app_equivalence import inventory, verify as verify_equivalence
from verify_small_app_bundle import verify as verify_small_app



def render(staging: Path, online_app: Path, output: Path):
    private_parent(staging)
    private_parent(output.parent)
    if output.suffix.lower() != '.dmg' or os.path.lexists(output):
        raise ValueError('Choose a new DMG path')
    if output.is_relative_to(staging):
        raise ValueError('DMG must be outside staging')
    marker = json.loads((staging / 'STAGING-VERIFIED.json').read_text())
    if marker.get('status') != 'STAGING_VERIFIED_NOT_RELEASED':
        raise ValueError('Completed staging marker required')
    app = staging / 'Vox Agent.app'
    current = hashlib.sha256(json.dumps(inventory(app), sort_keys=True).encode()).hexdigest()
    if marker.get('app_inventory_sha256') != current:
        raise ValueError('Staging App fingerprint changed')
    verify_small_app(app)
    verify_equivalence(online_app, app)
    assets = assets_inventory(staging / 'OfflineAssets')
    if marker.get('model_file_sha256') != assets:
        raise ValueError('Staging model snapshot changed')
    work = Path(tempfile.mkdtemp(prefix='.split-dmg-', dir=output.parent))
    image = work / 'media.dmg'
    mount = work / 'mounted'
    mount.mkdir()
    attached = False
    try:
        subprocess.run(['/usr/bin/hdiutil', 'create', '-srcfolder', str(staging), '-format', 'UDZO',
                        '-volname', 'Vox Agent Offline Staging', '-noskipunreadable', str(image)],
                       check=True, capture_output=True, timeout=600)
        attached = True  # Attach failures can still leave a partial mount.
        result = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-noautoopen', '-plist',
                                 '-mountpoint', str(mount), str(image)], check=True, capture_output=True, timeout=120)
        attached = True
        details = plistlib.loads(result.stdout)
        if not any(item.get('mount-point') == str(mount) for item in details.get('system-entities', [])):
            raise ValueError('Unexpected mount result')
        verify_small_app(mount / 'Vox Agent.app')
        verify_equivalence(online_app, mount / 'Vox Agent.app')
        if assets_inventory(mount / 'OfflineAssets') != assets:
            raise ValueError('Mounted offline assets differ')
        subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=120)
        attached = False
        with image.open('rb') as incoming:
            digest = hashlib.file_digest(incoming, 'sha256').hexdigest()
        os.link(image, output, follow_symlinks=False)
        return {'dmg': str(output), 'sha256': digest, 'mounted_app_and_model_snapshot_verified': True,
                'signed_release': False, 'model_authenticity': 'receiving App compiled-catalog verification required'}
    finally:
        if attached:
            try:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=120)
                attached = False
            except (subprocess.SubprocessError, OSError):
                pass
        if attached:
            # Preserve work/image/mount when detach cannot be confirmed; never delete through a mount.
            print('Mounted work retained for operator detach: ' + str(work))
        else:
            shutil.rmtree(work)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging', required=True, type=Path)
    parser.add_argument('--online-app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.staging, args.online_app, args.output), sort_keys=True))
