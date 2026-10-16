"""Opt-in real hdiutil create/read-only mount/detach of tiny synthetic media only."""
import json
import hashlib
from pathlib import Path
import shutil
import tempfile

from render_split_media_dmg import render
from verify_app_equivalence import inventory
from split_media_manifest import assets_inventory


def main():
    # Do not auto-remove this root on failure: renderer can retain an uncertain mount.
    root = Path(tempfile.mkdtemp(prefix='vox-split-dmg-fixture-')).resolve()
    online = root / 'Online.app'
    binary = online / 'Contents/MacOS/VoxLog'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic executable; NEVER EXECUTE')
    binary.chmod(0o755)
    staging = root / 'staging'
    staging.mkdir(mode=0o700)
    shutil.copytree(online, staging / 'Vox Agent.app')
    assets = staging / 'OfflineAssets/model'
    assets.mkdir(parents=True)
    (assets / 'config.json').write_bytes(b'synthetic model; not production catalog')
    (staging / 'STAGING-VERIFIED.json').write_text(json.dumps({'status': 'STAGING_VERIFIED_NOT_RELEASED', 'app_inventory_sha256': hashlib.sha256(json.dumps(inventory(staging / 'Vox Agent.app'), sort_keys=True).encode()).hexdigest(), 'model_file_sha256': assets_inventory(staging / 'OfflineAssets')}))
    try:
        report = render(staging, online, root / 'fixture.dmg')
    except BaseException:
        print('NOT_PASS: fixture retained for inspection: ' + str(root))
        raise
    print(json.dumps(report, sort_keys=True))
    shutil.rmtree(root)  # Success means the renderer confirmed detach; synthetic data only.


if __name__ == '__main__':
    main()
