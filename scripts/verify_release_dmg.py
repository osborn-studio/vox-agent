"""Read-only validation of the actual standard DMG before checksum/upload."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from verify_small_app_bundle import verify


def verify_dmg(dmg: Path):
    work = Path(tempfile.mkdtemp(prefix='vox-release-check-'))
    mount = work / 'mounted'
    mount.mkdir()
    attached = True  # A failed attach can still leave a mount behind.
    try:
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                        '-mountpoint', str(mount), str(dmg.resolve())], check=True, timeout=120)
        app = mount / 'Vox Agent.app'
        verify(app, require_qwen_runtime=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)],
                       check=True, timeout=120)
        subprocess.run([str(app / 'Contents/MacOS/qwen3-asr-server'), '--help'],
                       check=True, timeout=120)
    finally:
        try:
            subprocess.run(['hdiutil', 'detach', str(mount)], check=True, timeout=120)
            attached = False
        finally:
            if attached:
                print(f'Mount may still be active; retained for inspection: {work}')
            else:
                shutil.rmtree(work)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dmg', required=True, type=Path)
    verify_dmg(parser.parse_args().dmg)
    print('PASS: mounted DMG includes signed app, Qwen runtime and licenses; helper starts')
