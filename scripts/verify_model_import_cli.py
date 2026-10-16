"""Exercise the built CLI's failure exits on synthetic data; no real models/network."""

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    binary = root / "build/model-import/model-import"
    with tempfile.TemporaryDirectory(prefix="vox-cli-smoke-") as temporary:
        base = Path(temporary).resolve()
        # Replace the catalog only in an isolated test binary, never the production CLI.
        payload = b"synthetic model bytes"
        entries = [{"path": "config.json", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]
        digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        stub = base / 'QwenModelCatalog.swift'
        stub.write_text(f'''import Foundation
        enum QwenModelCatalog {{
          static let modelID = "qwen3-asr-0.6b", revision = "fixture-only", digest = "{digest}"
          static func manifest() throws -> ModelManifest {{
            try ModelManifest(files: [.init(path: "config.json", size: {len(payload)}, sha256: "{entries[0]['sha256']}")], expectedDigest: digest)
          }}
        }}''')
        fixture_binary = base / 'fixture-cli'
        subprocess.run(['swiftc', '-D', 'MODEL_IMPORT_CLI', '-sdk', args.sdk, '-swift-version', '5',
                        '-parse-as-library', *[str(root / f'macos/VoxLog/Services/{name}.swift')
                        for name in ['ModelManifest', 'ModelTreeVerifier', 'ModelStore', 'ASRComponentResolver']],
                        str(stub), str(root / 'scripts/ModelImportCLI.swift'), '-o', str(fixture_binary)],
                       check=True, timeout=90)
        fixture_source = base / 'fixture-source'
        fixture_source.mkdir(mode=0o700)
        (fixture_source / 'config.json').write_bytes(payload)
        fixture_parent = base / 'fixture-media'
        fixture_parent.mkdir(mode=0o700)
        fixture_store = base / 'fixture-store'
        fixture_store.mkdir(mode=0o700)
        output = subprocess.run([str(fixture_binary), 'media', str(fixture_parent), str(fixture_source)],
                                check=True, capture_output=True, text=True, timeout=30)
        report = json.loads(output.stdout)
        assert report['operation'] == 'media' and report['digest'] == digest
        media_path = Path(report['verified_path'])
        assert media_path.parent == fixture_parent and (media_path / 'config.json').read_bytes() == payload
        imported = subprocess.run([str(fixture_binary), 'import', str(fixture_store), str(media_path)],
                                  check=True, capture_output=True, text=True, timeout=30)
        receipt = json.loads(imported.stdout)
        assert receipt['operation'] == 'import'
        assert Path(receipt['verified_path']).is_relative_to(fixture_store)
        verified = subprocess.run([str(fixture_binary), 'verify', str(fixture_store)],
                                  check=True, capture_output=True, text=True, timeout=30)
        assert json.loads(verified.stdout)['verified_path'] == receipt['verified_path']
        assert (Path(receipt['verified_path']) / 'config.json').read_bytes() == payload
        assert (fixture_source / 'config.json').read_bytes() == payload
        assert (media_path / 'config.json').read_bytes() == payload
        archive_parent = base / 'archive-output'
        archive_parent.mkdir(mode=0o700)
        archive_path = archive_parent / 'fixture.zip'
        packaged = subprocess.run([sys.executable, str(root / 'scripts/package_model_media.py'),
                                   '--cli', str(fixture_binary), '--source', str(fixture_source),
                                   '--staging-parent', str(fixture_parent), '--output', str(archive_path)],
                                  check=True, capture_output=True, text=True, timeout=30)
        package_report = json.loads(packaged.stdout)
        assert Path(package_report['retained_media']).is_dir()
        extracted = base / 'extracted-media'
        extracted.mkdir(mode=0o700)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.namelist() == ['config.json']
            (extracted / 'config.json').write_bytes(archive.read('config.json'))
        receiving_store = base / 'receiving-store'
        receiving_store.mkdir(mode=0o700)
        restored = subprocess.run([str(fixture_binary), 'import', str(receiving_store), str(extracted)],
                                 check=True, capture_output=True, text=True, timeout=30)
        assert (Path(json.loads(restored.stdout)['verified_path']) / 'config.json').read_bytes() == payload
        assert archive_path.exists() and fixture_source.exists()
        # Actual producer + actual compiled fixture CLI, not a mocked receipt.
        fixture_app = base / 'Fixture.app'
        app_bin = fixture_app / 'Contents/MacOS'
        app_bin.mkdir(parents=True)
        for name in ['VoxLog', 'qwen3-asr-server']:
            (app_bin / name).write_bytes(b'fixture executable, never launched')
            (app_bin / name).chmod(0o755)
        app_resources = fixture_app / 'Contents/Resources'
        app_resources.mkdir()
        (app_resources / 'QWEN_MODEL_CATALOG.json').write_text(json.dumps({'model_id': 'qwen3-asr-0.6b', 'revision': 'fixture-only', 'digest': digest}))
        fixture_licenses = app_resources / 'QwenRuntimeLicenses'
        fixture_licenses.mkdir()
        (fixture_licenses / 'LICENSE.txt').write_bytes(b'fixture notices')
        (app_resources / 'QWEN_RUNTIME_PREPARED.json').write_text(json.dumps({
            'status': 'PREPARED_REQUIRES_SIGNING',
            'helper_sha256': hashlib.sha256((app_bin / 'qwen3-asr-server').read_bytes()).hexdigest(),
            'model_identity': {'model_id': 'qwen3-asr-0.6b', 'revision': 'fixture-only', 'digest': digest},
            'notice_file_sha256': {'LICENSE.txt': hashlib.sha256(b'fixture notices').hexdigest()}}))
        subprocess.run(['/usr/bin/xattr', '-w', 'com.osborn.vox.fixture', 'fixture-value', str(app_bin / 'VoxLog')], check=True)
        split_root = base / 'split-media'
        produced = subprocess.run([sys.executable, str(root / 'scripts/build_split_model_media.py'),
                                  '--cli', str(fixture_binary), '--source-app', str(fixture_app),
                                  '--model-source', str(fixture_source), '--output', str(split_root)],
                                 check=True, capture_output=True, text=True, timeout=30)
        split_receipt = json.loads(produced.stdout)
        attribute = subprocess.run(['/usr/bin/xattr', '-p', 'com.osborn.vox.fixture', str(split_root / 'Vox Agent.app/Contents/MacOS/VoxLog')], check=True, capture_output=True, text=True)
        assert attribute.stdout.strip() == 'fixture-value'
        assert split_receipt['status'] == 'STAGING_VERIFIED_NOT_RELEASED'
        assert split_receipt['signing_verified'] is False
        assert (split_root / split_receipt['model'] / 'config.json').read_bytes() == payload
        assert (split_root / 'Vox Agent.app/Contents/MacOS/VoxLog').read_bytes() == (app_bin / 'VoxLog').read_bytes()
        assert not (split_root / 'Vox Agent.app/Contents/Resources/Models').exists()
        assert (split_root / 'STAGING-VERIFIED.json').is_file()
        # Prepare one common App from a cloud fixture, then feed it to both media lanes.
        cloud_app = base / 'Cloud.app'
        shutil.copytree(fixture_app, cloud_app)
        (cloud_app / 'Contents/Resources/QWEN_RUNTIME_PREPARED.json').unlink()
        shutil.rmtree(cloud_app / 'Contents/Resources/QwenRuntimeLicenses')
        (cloud_app / 'Contents/MacOS/qwen3-asr-server').unlink()  # Owned fixture only.
        supplied_helper = base / 'supplied-runtime-helper'
        supplied_helper.write_bytes(b'synthetic runtime helper NEVER EXECUTE')
        supplied_helper.chmod(0o755)
        notices = base / 'runtime-notices'
        notices.mkdir()
        (notices / 'LICENSE.txt').write_text('synthetic license fixture, not real license evidence')
        common_app = base / 'Common.app'
        prepared = subprocess.run([sys.executable, str(root / 'scripts/prepare_common_asr_app.py'),
                                   '--source-app', str(cloud_app), '--runtime-helper', str(supplied_helper),
                                   '--runtime-notices', str(notices), '--output-app', str(common_app)],
                                  check=True, capture_output=True, text=True, timeout=30)
        assert json.loads(prepared.stdout)['requires_signing'] is True
        assert not (cloud_app / 'Contents/MacOS/qwen3-asr-server').exists()
        assert (common_app / 'Contents/MacOS/qwen3-asr-server').read_bytes() == supplied_helper.read_bytes()
        common_media = base / 'common-offline-media'
        subprocess.run([sys.executable, str(root / 'scripts/build_split_model_media.py'), '--cli', str(fixture_binary),
                        '--source-app', str(common_app), '--model-source', str(fixture_source), '--output', str(common_media)],
                       check=True, capture_output=True, text=True, timeout=30)
        assert (common_media / 'STAGING-VERIFIED.json').is_file()
        store = base / "store"
        source = base / "source"
        store.mkdir(mode=0o700)
        media = base / "media"
        media.mkdir(mode=0o700)
        source.mkdir(mode=0o700)
        for arguments, expected in [
            ([], 2),
            (["verify", str(store)], 1),
            (["verify", str(base / "absent")], 1),
            (["import", str(store)], 2),
            (["media", str(store)], 2),
            (["media", str(base / "media"), str(source)], 1),
            (["import", str(store), str(source)], 1),
        ]:
            result = subprocess.run(
                [str(binary), *arguments], capture_output=True, text=True, timeout=15
            )
            assert result.returncode == expected, result.stderr
            assert not result.stdout, "Failure must not emit a verified model receipt"
            if expected == 1:
                assert "Model operation failed: [" in result.stderr
                assert str(base) not in result.stderr, "Failure diagnostics must not leak source paths"
        with (store / ".writer.lock").open("rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([str(binary), "import", str(store), str(source)],
                                    capture_output=True, text=True, timeout=15)
            assert result.returncode == 1 and "[busy]" in result.stderr
            assert not result.stdout and str(base) not in result.stderr
            assert (store / ".writer.lock").exists()
        assert list((store / "staging").iterdir()) == []
        assert list(source.iterdir()) == []
        assert list(media.iterdir()) == [], "Failed media build must remove its own staging"
        assert not (base / "absent").exists()
        assert list((store / "models/qwen3-asr-0.6b").iterdir()) == []
    print("Model import CLI: fixture CLI media JSON-to-import success; 8 production failure cases and preservation checks passed")


if __name__ == "__main__":
    main()
