"""T02c: preflight is split into source / host / sku stages so source checks can
pass on a macOS dev host while host and full-SKU packaging checks fail closed."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_windows_release_preflight import main, qualify  # noqa: E402

TRIPLE = 'x86_64-pc-windows-msvc'


def pe_bytes(machine: int, magic: int = 0x20B) -> bytes:
    """Minimal structurally complete PE image: DOS header, COFF header,
    PE32+ optional header and one section header."""
    optional = struct.pack('<H', magic) + b'\0' * (0xF0 - 2)
    coff = struct.pack('<HHIIIHH', machine, 1, 0, 0, 0, len(optional), 0x0022)
    dos = bytearray(0x40)
    dos[0:2] = b'MZ'
    dos[0x3C:0x40] = struct.pack('<I', 0x40)
    return bytes(dos) + b'PE\0\0' + coff + optional + b'\0' * 40


def add_release_engine(tauri: Path) -> str:
    """Give a fixture the engine the full SKU requires: the release config,
    an x64 PE engine named for Tauri externalBin, and its matching manifest."""
    import hashlib
    (tauri / 'tauri.release-engine.conf.json').write_text(
        json.dumps({'bundle': {'externalBin': ['binaries/voxagent-engine']}}))
    name = f'voxagent-engine-{TRIPLE}.exe'
    (tauri / 'binaries').mkdir(exist_ok=True)
    (tauri / f'binaries/{name}').write_bytes(pe_bytes(0x8664))
    digest = hashlib.sha256((tauri / f'binaries/{name}').read_bytes()).hexdigest()
    (tauri / 'binaries/voxagent-engine.manifest.json').write_text(json.dumps(
        {'file': name, 'target': TRIPLE, 'sha256': digest, 'source_commit': 'a' * 40}))
    return f'binaries/{name}'


def make_repo(root: Path, version='4.2.8', bundle=None):
    shell = root / 'apps/desktop-tauri'
    (shell / 'src-tauri').mkdir(parents=True)
    (shell / 'package.json').write_text(json.dumps({'version': version}))
    lock = {'name': 'vox-agent-desktop-tauri', 'version': version,
            'packages': {'': {'name': 'vox-agent-desktop-tauri', 'version': version}}}
    (shell / 'package-lock.json').write_text(json.dumps(lock))
    (root / 'package-lock.json').write_text(json.dumps(
        {'packages': {'apps/desktop-tauri': {'name': 'vox-agent-desktop-tauri', 'version': version}}}))
    (shell / 'src-tauri/Cargo.toml').write_text(
        f'[package]\nname = "vox-agent-desktop-tauri"\nversion = "{version}"\n')
    (shell / 'src-tauri/Cargo.lock').write_text(
        f'[[package]]\nname = "vox-agent-desktop-tauri"\nversion = "{version}"\n')
    (shell / 'src-tauri/tauri.conf.json').write_text(json.dumps(
        {'version': version, 'bundle': bundle if bundle is not None else {'active': True}}))
    return shell / 'src-tauri'


class StageTests(unittest.TestCase):
    def test_source_stage_passes_on_macos_for_the_preview_sku(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp))
            result = qualify(Path(temp), '4.2.8', 'Darwin', sku='preview', stages=('source',))
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['stages'], {'source': 'PASS', 'host': 'NOT_RUN', 'sku': 'NOT_RUN'})
            self.assertEqual(result['installer_acceptance'], 'NOT_RUN')

    def test_source_stage_still_blocks_on_version_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp), version='4.1.0')
            result = qualify(Path(temp), '4.2.8', 'Darwin', sku='preview', stages=('source',))
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertIn('npm_version_mismatch', result['blockers'])

    def test_host_stage_never_passes_off_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp))
            result = qualify(Path(temp), '4.2.8', 'Darwin', sku='preview', stages=('source', 'host'))
            self.assertEqual(result['stages']['host'], 'BLOCKED')
            self.assertIn('windows_host_required', result['blockers'])

    def test_preview_sku_does_not_require_a_bundled_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp))
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='preview', stages=('source', 'host', 'sku'))
            self.assertEqual(result['status'], 'PASS', result['blockers'])
            self.assertEqual(result['bundled_artifacts'], [])

    def test_full_sku_requires_a_declaration(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp))
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn('bundled_runtime_not_declared', result['blockers'])

    def test_full_sku_declared_external_binary_must_exist_with_target_triple(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp), bundle={'active': True, 'externalBin': ['binaries/vox-engine']})
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn(f'bundled_file_missing:binaries/vox-engine-{TRIPLE}.exe', result['blockers'])

    def test_full_sku_rejects_wrong_architecture_and_non_pe_files(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), bundle={'active': True, 'externalBin': ['binaries/a', 'binaries/b']})
            (tauri / 'binaries').mkdir()
            (tauri / f'binaries/a-{TRIPLE}.exe').write_bytes(pe_bytes(0xAA64))  # ARM64
            (tauri / f'binaries/b-{TRIPLE}.exe').write_bytes(b'#!/bin/sh\n')
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn(f'bundled_file_wrong_architecture:binaries/a-{TRIPLE}.exe', result['blockers'])
            self.assertIn(f'bundled_file_not_pe:binaries/b-{TRIPLE}.exe', result['blockers'])

    def test_full_sku_valid_x64_binary_and_resource_pass_with_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), bundle={'active': True, 'resources': ['runtime/**/*']})
            engine = add_release_engine(tauri)
            (tauri / 'runtime/lib').mkdir(parents=True)
            (tauri / 'runtime/lib/helper.dll').write_bytes(pe_bytes(0x8664))
            (tauri / 'runtime/lib/data.bin').write_bytes(b'data')
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('source', 'host', 'sku'))
            self.assertEqual(result['status'], 'PASS', result['blockers'])
            paths = {item['path']: item for item in result['bundled_artifacts']}
            self.assertEqual(set(paths), {engine, 'runtime/lib/helper.dll', 'runtime/lib/data.bin'})
            self.assertEqual(len(paths['runtime/lib/data.bin']['sha256']), 64)
            self.assertEqual(paths['runtime/lib/helper.dll']['machine'], 'x64')

    # T02/T29: the engine is declared in the release config passed to
    # `tauri build --config`; the preflight checks what packaging will bundle.
    def release_engine(self, root: Path, manifest=None, binary=True):
        tauri = make_repo(root, version='4.2.9')
        (tauri / 'tauri.release-engine.conf.json').write_text(
            json.dumps({'bundle': {'externalBin': ['binaries/voxagent-engine']}}))
        name = f'voxagent-engine-{TRIPLE}.exe'
        if binary:
            (tauri / 'binaries').mkdir()
            (tauri / f'binaries/{name}').write_bytes(pe_bytes(0x8664))
        if manifest is not None:
            import hashlib
            digest = hashlib.sha256((tauri / f'binaries/{name}').read_bytes()).hexdigest()
            data = {'file': name, 'target': TRIPLE, 'sha256': digest, 'source_commit': 'a' * 40}
            data.update(manifest)
            (tauri / 'binaries/voxagent-engine.manifest.json').write_text(json.dumps(data))
        return tauri

    def test_the_release_engine_config_is_what_the_full_sku_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            self.release_engine(Path(temp), binary=False)
            result = qualify(Path(temp), '4.2.9', 'Windows', sku='full', stages=('sku',))
            self.assertNotIn('bundled_runtime_not_declared', result['blockers'])
            self.assertIn(f'bundled_file_missing:binaries/voxagent-engine-{TRIPLE}.exe', result['blockers'])
            self.assertEqual(result['release_config'], 'tauri.release-engine.conf.json')

    def test_the_bundled_engine_needs_a_matching_manifest(self):
        name = f'voxagent-engine-{TRIPLE}.exe'
        for manifest, blocker in [
            (None, f'bundled_engine_manifest_missing:{name}'),
            ({'sha256': '0' * 64}, f'bundled_engine_manifest_mismatch:{name}'),
            ({'target': 'aarch64-pc-windows-msvc'}, f'bundled_engine_manifest_mismatch:{name}'),
            ({'file': 'other.exe'}, f'bundled_engine_manifest_mismatch:{name}'),
        ]:
            with tempfile.TemporaryDirectory() as temp:
                self.release_engine(Path(temp), manifest=manifest)
                result = qualify(Path(temp), '4.2.9', 'Windows', sku='full', stages=('sku',))
                self.assertIn(blocker, result['blockers'], manifest)
        with tempfile.TemporaryDirectory() as temp:
            self.release_engine(Path(temp), manifest={})
            result = qualify(Path(temp), '4.2.9', 'Windows', sku='full', stages=('source', 'host', 'sku'))
            self.assertEqual(result['status'], 'PASS', result['blockers'])

    def test_the_full_sku_requires_the_engine_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), version='4.2.9',
                              bundle={'active': True, 'resources': ['runtime/*']})
            (tauri / 'runtime').mkdir()
            (tauri / 'runtime/data.bin').write_bytes(b'data')
            result = qualify(Path(temp), '4.2.9', 'Windows', sku='full', stages=('sku',))
            self.assertIn('release_config_missing', result['blockers'])
            self.assertIn('bundled_engine_not_declared', result['blockers'])

    def test_the_release_config_replaces_external_bin_like_tauri_merge(self):
        # Tauri merges --config with JSON merge patch: arrays are replaced.
        with tempfile.TemporaryDirectory() as temp:
            tauri = self.release_engine(Path(temp), manifest={})
            config = json.loads((tauri / 'tauri.conf.json').read_text())
            config['bundle']['externalBin'] = ['binaries/stale-helper']
            (tauri / 'tauri.conf.json').write_text(json.dumps(config))
            result = qualify(Path(temp), '4.2.9', 'Windows', sku='full', stages=('sku',))
            self.assertEqual(result['status'], 'PASS', result['blockers'])
            self.assertNotIn('binaries/stale-helper', json.dumps(result))

    def test_an_unreadable_release_config_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), version='4.2.9')
            (tauri / 'tauri.release-engine.conf.json').write_text('{not json')
            result = qualify(Path(temp), '4.2.9', 'Windows', sku='preview', stages=('sku',))
            self.assertIn('release_config_unreadable', result['blockers'])

    def test_resource_pattern_matching_nothing_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp), bundle={'active': True, 'resources': ['runtime/**/*']})
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn('bundled_resource_unmatched:runtime/**/*', result['blockers'])

    def test_resource_paths_cannot_escape_the_repository(self):
        # src-tauri -> desktop-tauri -> apps -> repo root; one more level escapes it.
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp), bundle={'active': True, 'resources': ['../../../../etc/*']})
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn('bundled_path_outside_repository:../../../../etc/*', result['blockers'])

    def test_repository_build_outputs_referenced_with_parent_paths_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tauri = make_repo(root, bundle={'active': True, 'resources': ['../../../dist/engine/*']})
            engine = add_release_engine(tauri)
            (root / 'dist/engine').mkdir(parents=True)
            (root / 'dist/engine/engine.dll').write_bytes(pe_bytes(0x8664))
            result = qualify(root, '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertEqual(result['status'], 'PASS', result['blockers'])
            self.assertEqual([a['path'] for a in result['bundled_artifacts']],
                             [engine, '../../../dist/engine/engine.dll'])

    def test_truncated_or_incomplete_pe_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), bundle={'active': True, 'externalBin': ['b/short', 'b/x86', 'b/huge']})
            (tauri / 'b').mkdir()
            valid = pe_bytes(0x8664)
            (tauri / f'b/short-{TRIPLE}.exe').write_bytes(valid[:0x40 + 4 + 2])  # signature + machine only
            (tauri / f'b/x86-{TRIPLE}.exe').write_bytes(pe_bytes(0x8664, magic=0x10B))  # PE32 header on x64
            huge = bytearray(valid)
            huge[0x3C:0x40] = struct.pack('<I', 0x7FFFFFFF)
            (tauri / f'b/huge-{TRIPLE}.exe').write_bytes(bytes(huge))
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            for name in ('short', 'x86', 'huge'):
                self.assertIn(f'bundled_file_not_pe:b/{name}-{TRIPLE}.exe', result['blockers'])

    def test_symlinks_resolving_outside_the_repository_are_blocked_not_dropped(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            tauri = make_repo(root, bundle={'active': True, 'resources': ['runtime', 'globbed/*']})
            Path(outside, 'secret.dll').write_bytes(pe_bytes(0x8664))
            (tauri / 'runtime').mkdir()
            (tauri / 'runtime/ok.bin').write_bytes(b'ok')
            (tauri / 'runtime/escape.dll').symlink_to(Path(outside, 'secret.dll'))
            (tauri / 'globbed').mkdir()
            (tauri / 'globbed/ok.bin').write_bytes(b'ok')
            (tauri / 'globbed/escape.dll').symlink_to(Path(outside, 'secret.dll'))
            result = qualify(root, '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn('bundled_path_outside_repository:runtime', result['blockers'])
            self.assertIn('bundled_path_outside_repository:globbed/*', result['blockers'])

    def test_double_star_matches_files_in_nested_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), bundle={'active': True, 'resources': ['engine/**']})
            engine = add_release_engine(tauri)
            (tauri / 'engine/lib').mkdir(parents=True)
            (tauri / 'engine/top.bin').write_bytes(b'1')
            (tauri / 'engine/lib/nested.bin').write_bytes(b'2')
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertEqual(result['status'], 'PASS', result['blockers'])
            self.assertEqual(sorted(a['path'] for a in result['bundled_artifacts']),
                             sorted([engine, 'engine/lib/nested.bin', 'engine/top.bin']))

    def test_resource_map_destinations_must_be_relative_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            tauri = make_repo(Path(temp), bundle={'active': True, 'resources': {'data.bin': None}})
            (tauri / 'data.bin').write_bytes(b'd')
            result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
            self.assertIn('invalid_bundle_declaration', result['blockers'])
            for bad in ('/abs/data.bin', '../outside.bin'):
                make = json.loads((tauri / 'tauri.conf.json').read_text())
                make['bundle']['resources'] = {'data.bin': bad}
                (tauri / 'tauri.conf.json').write_text(json.dumps(make))
                result = qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))
                self.assertIn('invalid_bundle_declaration', result['blockers'], bad)
            make['bundle']['resources'] = {'data.bin': 'resources/data.bin'}
            (tauri / 'tauri.conf.json').write_text(json.dumps(make))
            add_release_engine(tauri)
            self.assertEqual(qualify(Path(temp), '4.2.8', 'Windows', sku='full', stages=('sku',))['status'], 'PASS')

    def test_cli_source_stage_passes_for_this_repository_on_any_host(self):
        self.assertEqual(main(['--version', '4.2.9', '--sku', 'preview', '--stage', 'source']), 0)

    def test_cli_defaults_to_every_stage_and_the_full_sku(self):
        result = qualify(Path(__file__).resolve().parents[1], '4.2.9', 'Darwin')
        self.assertEqual(result['sku'], 'full')
        self.assertEqual(set(result['stages']), {'source', 'host', 'sku'})
        self.assertEqual(result['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
