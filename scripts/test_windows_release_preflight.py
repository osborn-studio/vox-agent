import json
import struct
import tempfile
import unittest
from pathlib import Path

from test_windows_preflight_stages import add_release_engine  # noqa: E402
from verify_windows_release_preflight import qualify


class PreflightTests(unittest.TestCase):
    def fixture(self, root, version='4.2.7', bundle=None, cargo_version=None,
                cargo_lock_version=None, npm_lock_version=None, root_lock_version=None,
                release_engine=True):
        shell = root / 'apps/desktop-tauri'
        (shell / 'src-tauri').mkdir(parents=True)
        (shell / 'package.json').write_text(json.dumps({'version': version}))
        (shell / 'package-lock.json').write_text(json.dumps({
            'name': 'vox-agent-desktop-tauri', 'version': npm_lock_version or version,
            'packages': {'': {'name': 'vox-agent-desktop-tauri', 'version': npm_lock_version or version}}}))
        (root / 'package-lock.json').write_text(json.dumps({
            'packages': {'apps/desktop-tauri': {'name': 'vox-agent-desktop-tauri',
                                                 'version': root_lock_version or version},
                         'node_modules/secure-json-parse': {'version': '4.1.0'}}}))
        (shell / 'src-tauri/Cargo.toml').write_text(
            f'[package]\nname = "vox-agent-desktop-tauri"\nversion = "{cargo_version or version}"\n')
        (shell / 'src-tauri/Cargo.lock').write_text(
            '[[package]]\nname = "other"\nversion = "4.1.0"\n\n[[package]]\nname = "vox-agent-desktop-tauri"\n'
            f'version = "{cargo_lock_version or version}"\n')
        # A declared runtime must exist: write it as a minimal x64 PE image.
        optional = struct.pack('<H', 0x20B) + b'\0' * (0xF0 - 2)
        coff = struct.pack('<HHIIIHH', 0x8664, 1, 0, 0, 0, len(optional), 0x0022)
        header = bytearray(0x40)
        header[0:2] = b'MZ'
        header[0x3C:0x40] = struct.pack('<I', 0x40)
        (shell / 'src-tauri/bin').mkdir()
        (shell / 'src-tauri/bin/voxagent-engine.exe').write_bytes(
            bytes(header) + b'PE\0\0' + coff + optional + b'\0' * 40)
        (shell / 'src-tauri/tauri.conf.json').write_text(json.dumps({
            'version': version,
            'bundle': {'active': True, 'resources': ['bin/voxagent-engine.exe']}
            if bundle is None else bundle,
        }))
        # The full SKU ships the engine through the release config (T02/T29).
        if release_engine:
            add_release_engine(shell / 'src-tauri')

    def test_macos_never_qualifies_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            result = qualify(root, '4.2.7', 'Darwin')
            self.assertIn('windows_host_required', result['blockers'])
            self.assertEqual(result['release_acceptance'], 'NOT_RUN')

    def test_version_and_missing_runtime_block_clean_machine_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.1.0', {'active': True, 'targets': 'all'}, release_engine=False)
            result = qualify(root, '4.2.7', 'Windows')
            self.assertEqual(set(result['blockers']), {
                'npm_version_mismatch', 'tauri_version_mismatch',
                'cargo_version_mismatch', 'cargo_lock_version_mismatch',
                'npm_lock_version_mismatch', 'root_npm_lock_version_mismatch',
                'bundled_runtime_not_declared', 'release_config_missing', 'bundled_engine_not_declared',
            })

    def test_static_pass_still_has_no_installation_or_parity_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            result = qualify(root, '4.2.7', 'Windows')
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['installer_acceptance'], 'NOT_RUN')
            self.assertEqual(result['authenticode'], 'NOT_RUN')

    def test_cargo_and_lockfile_versions_must_match_too(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8', cargo_version='4.1.0', cargo_lock_version='4.1.0',
                         npm_lock_version='4.1.0', root_lock_version='4.1.0')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertEqual(set(result['blockers']), {
                'cargo_version_mismatch', 'cargo_lock_version_mismatch',
                'npm_lock_version_mismatch', 'root_npm_lock_version_mismatch',
            })
            self.assertEqual(result['source_versions']['cargo'], '4.1.0')

    def test_unrelated_dependency_versions_do_not_satisfy_or_block_the_check(self):
        # secure-json-parse 4.1.0 in the root lock and "other" 4.1.0 in Cargo.lock
        # must not be mistaken for the desktop package version.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertEqual(result['blockers'], [])
            self.assertEqual(result['source_versions'], {
                'npm': '4.2.8', 'tauri': '4.2.8', 'cargo': '4.2.8',
                'cargo_lock': '4.2.8', 'npm_lock': '4.2.8', 'root_npm_lock': '4.2.8'})

    def test_desktop_lock_top_level_and_root_package_must_both_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8')
            lock = root / 'apps/desktop-tauri/package-lock.json'
            data = json.loads(lock.read_text())
            data['version'] = '4.1.0'  # nested packages[''] stays 4.2.8
            lock.write_text(json.dumps(data))
            result = qualify(root, '4.2.8', 'Windows')
            self.assertEqual(result['blockers'], ['npm_lock_version_mismatch'])

    def test_cargo_toml_with_comment_and_reordered_lock_entry_parse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8')
            cargo = root / 'apps/desktop-tauri/src-tauri'
            (cargo / 'Cargo.toml').write_text(
                '[workspace]\nmembers = []\n\n[package] # desktop\nname = "vox-agent-desktop-tauri"\n'
                'edition = "2021"\nversion = "4.2.8"\n')
            (cargo / 'Cargo.lock').write_text(
                '[[package]]\nversion = "4.2.8"\nname = "vox-agent-desktop-tauri"\n')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertEqual(result['blockers'], [])

    def test_workspace_inherited_or_duplicate_versions_block_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8')
            cargo = root / 'apps/desktop-tauri/src-tauri'
            (cargo / 'Cargo.toml').write_text('[package]\nname = "x"\nversion.workspace = true\n')
            (cargo / 'Cargo.lock').write_text(
                '[[package]]\nname = "vox-agent-desktop-tauri"\nversion = "4.2.8"\n\n'
                '[[package]]\nname = "vox-agent-desktop-tauri"\nversion = "4.1.0"\n')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertIn('cargo_version_mismatch', result['blockers'])
            self.assertIn('cargo_lock_version_mismatch', result['blockers'])

    def test_wrong_toml_shapes_block_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '4.2.8')
            cargo = root / 'apps/desktop-tauri/src-tauri'
            (cargo / 'Cargo.toml').write_text('package = 1\n')
            (cargo / 'Cargo.lock').write_text(
                'package = 1\n')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertIn('cargo_version_mismatch', result['blockers'])
            self.assertIn('cargo_lock_version_mismatch', result['blockers'])
            (cargo / 'Cargo.lock').write_text(
                '[[package]]\nname = "vox-agent-desktop-tauri"\nversion = { workspace = true }\n')
            result = qualify(root, '4.2.8', 'Windows')
            self.assertIn('cargo_lock_version_mismatch', result['blockers'])

    def test_missing_configuration_fails_closed_without_leaking_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            result = qualify(Path(temp), '4.2.7', 'Windows')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertNotIn(temp, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
