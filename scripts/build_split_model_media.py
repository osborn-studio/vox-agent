"""Create-only offline staging tree with unchanged App and model outside App.

No signing, App execution, DMG mounting, installation, upload or source deletion.
"""
import argparse
import json
import os
from pathlib import Path
import hashlib
import stat
import subprocess
import sys

from split_media_manifest import assets_inventory, verify_runtime_preparation
from verify_app_equivalence import inventory, verify as verify_equivalence
from verify_small_app_bundle import verify as verify_small_app


def private_parent(path: Path):
    if path != path.resolve() or not path.is_dir():
        raise ValueError('Use an existing physical output parent')
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Output parent must be owned and private')


def build(source_app: Path, model_source: Path, output: Path, cli: Path):
    private_parent(output.parent)
    if os.path.lexists(output):
        raise FileExistsError('Output must be new; incomplete builds are retained')
    if source_app != source_app.resolve() or not source_app.name.endswith('.app'):
        raise ValueError('Use a physical App path')
    if output.is_relative_to(source_app) or output.is_relative_to(model_source.resolve()):
        raise ValueError('Output must be outside the original sources')
    cli_info = cli.lstat()
    if cli != cli.resolve() or not stat.S_ISREG(cli_info.st_mode) or cli_info.st_uid != os.getuid() or cli_info.st_mode & 0o022:
        raise ValueError('Use an ordinary owned private-write pinned CLI')
    if not model_source.is_dir() or model_source.is_symlink():
        raise ValueError('Ordinary model source required')
    expected = json.loads((source_app / 'Contents/Resources/QWEN_MODEL_CATALOG.json').read_text())
    if not isinstance(expected, dict) or set(expected) != {'model_id', 'revision', 'digest'} or any(not isinstance(v, str) or not v for v in expected.values()):
        raise ValueError('Prepared App catalog identity required')
    verify_small_app(source_app)
    before = inventory(source_app)
    if any(entry[0] != 'link' and entry[1] & 0o022 for entry in before.values()):
        raise ValueError('Prepared App must not be group/world writable')
    helper = source_app / 'Contents/MacOS/qwen3-asr-server'
    helper_info = helper.lstat()
    if not stat.S_ISREG(helper_info.st_mode) or not helper_info.st_mode & 0o111:
        raise ValueError('Prepared App requires an ordinary executable Qwen helper')
    verify_runtime_preparation(source_app, expected)
    # Presence is a packaging precondition, not evidence of helper/runtime readiness.
    output.mkdir(mode=0o700)
    copied_app = output / 'Vox Agent.app'
    try:
        subprocess.run(['/usr/bin/ditto', '--rsrc', '--extattr', '--acl', str(source_app), str(copied_app)],
                       check=True, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        assets = output / 'OfflineAssets'
        assets.mkdir(mode=0o700)
        result = subprocess.run([str(cli), 'media', str(assets), str(model_source)],
                                capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(result.stderr or 'Pinned media utility failed')
        receipt = json.loads(result.stdout)
        model = Path(receipt['verified_path'])
        if receipt['operation'] != 'media' or model.parent != assets or not model.is_dir() or model.is_symlink():
            raise ValueError('Unexpected pinned media receipt')
        if {key: receipt.get(key) for key in expected} != expected:
            raise ValueError('CLI catalog differs from prepared App')
        if list(assets.iterdir()) != [model]:
            raise ValueError('Unexpected additional offline assets')
        verify_small_app(copied_app)
        verify_equivalence(source_app, copied_app)
        if inventory(source_app) != before:
            raise ValueError('Original App changed during build')
        guide = ('Offline staging only, not a signed release.\n'
                 'Keep your original App and model. Do not drag-replace a running App.\n'
                 'Use the new App settings folder importer on OfflineAssets/' + model.name + '.\n'
                 'The receiving App verifies its compiled catalog independently.\n'
                 'Confirm offline synthetic recognition and preserved login/outbox before cleanup.\n'
                 'Signing/notarization, runtime notices and mounted-DMG/Finder acceptance remain required.\n')
        (output / 'IMPORT-INSTRUCTIONS.txt').write_text(guide)
        report = {'schema_version': 1, 'status': 'STAGING_VERIFIED_NOT_RELEASED',
                  'app': 'Vox Agent.app', 'model': str(model.relative_to(output)),
                  'model_file_sha256': assets_inventory(assets),
                  'app_inventory_sha256': hashlib.sha256(json.dumps(inventory(copied_app), sort_keys=True).encode()).hexdigest(),
                  'durability_guaranteed': False, 'copy_method': 'macOS ditto --rsrc --extattr --acl',
                  'cli_reported_revision': receipt['revision'], 'cli_reported_digest': receipt['digest'],
                  'signing_verified': False, 'runtime_executed': False}
        # This marker is produced only after all checks; never trust an incomplete directory.
        temporary_marker = output / '.staging-marker.tmp'
        with temporary_marker.open('x') as marker:
            json.dump(report, marker, sort_keys=True, indent=2)
            marker.write('\n')
            marker.flush()
            os.fsync(marker.fileno())
        os.link(temporary_marker, output / 'STAGING-VERIFIED.json', follow_symlinks=False)
        temporary_marker.unlink()
        return report
    except BaseException:
        print('Incomplete staging retained: ' + str(output), file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app', type=Path, required=True)
    parser.add_argument('--model-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_app, args.model_source, args.output, args.cli), sort_keys=True))


if __name__ == '__main__':
    main()
