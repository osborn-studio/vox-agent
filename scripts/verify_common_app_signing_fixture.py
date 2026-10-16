"""Opt-in ad-hoc signature-order fixture; no production identity or App execution."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from prepare_common_asr_app import prepare
from split_media_manifest import verify_runtime_preparation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='vox-common-signing-fixture-') as temporary:
        root = Path(temporary).resolve()
        source = root / 'Tiny.swift'
        source.write_text('@main struct Fixture { static func main() {} }\n')
        helper = root / 'signed-helper'
        subprocess.run(['swiftc', '-sdk', args.sdk, '-parse-as-library', str(source), '-o', str(helper)], check=True, timeout=90)
        # Ad-hoc only, no certificate or timestamp service; NEVER runs the binary.
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(helper)], check=True, capture_output=True, timeout=30)
        app = root / 'Base.app'
        binary = app / 'Contents/MacOS/VoxLog'
        binary.parent.mkdir(parents=True)
        shutil.copy2(helper, binary)
        resources = app / 'Contents/Resources'
        resources.mkdir()
        identity = {'model_id': 'fixture', 'revision': 'fixture', 'digest': 'fixture'}
        (resources / 'QWEN_MODEL_CATALOG.json').write_text(json.dumps(identity))
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.osborn.vox.signing-fixture', 'CFBundleExecutable': 'VoxLog',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '0.0.0'}))
        notices = root / 'notices'
        notices.mkdir()
        (notices / 'LICENSE.txt').write_text('Synthetic notice; not real license evidence')
        common = root / 'Common.app'
        prepare(app, helper, notices, common)
        runtime = common / 'Contents/MacOS/qwen3-asr-server'
        before = hashlib.sha256(runtime.read_bytes()).hexdigest()
        # No --deep signing: helper was signed before its preparation record.
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(common)], check=True, capture_output=True, timeout=30)
        assert hashlib.sha256(runtime.read_bytes()).hexdigest() == before
        verify_runtime_preparation(common, identity)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(common)], check=True, capture_output=True, timeout=30)
        print(json.dumps({'adhoc_only': True, 'helper_hash_preserved': True, 'runtime_record_reverified': True,
                          'nested_signature_mechanics_verified': True, 'production_trust_verified': False,
                          'app_or_helper_executed': False}, sort_keys=True))


if __name__ == '__main__':
    main()
