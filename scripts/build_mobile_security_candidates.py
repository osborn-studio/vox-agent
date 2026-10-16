"""Rebuild private review candidates from hash-pinned, supplied upstream archives.
No network, dependency install, registry publish, or production manifest edits.
"""
import argparse
import base64
import difflib
import gzip
import platform
import os
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonicalize_compression(artifact, reference, identity):
    """Preserve immutable gzip bytes only after exact rebuilt tar-payload equality."""
    native = artifact.read_bytes()
    frozen = reference.read_bytes()
    def integrity(data):
        return 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if sha(frozen) != identity['sha256'] or integrity(frozen) != identity['integrity']:
        raise ValueError('Frozen artifact identity mismatch: canonical reference')
    if gzip.decompress(native) != gzip.decompress(frozen):
        return False
    if native != frozen:
        artifact.write_bytes(frozen)
        return True
    return False


def build():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    records = json.loads((repo / 'security/mobile-candidate/source-patches.json').read_text())
    expected = json.loads((repo / 'security/mobile-candidate/expected-artifacts.json').read_text())
    versions = {'python': platform.python_version(), 'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'npm': subprocess.check_output(['npm', '--version'], text=True).strip()}
    if versions != json.loads((repo / 'security/mobile-candidate/toolchain.json').read_text()):
        raise ValueError('Candidate build toolchain mismatch')
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipts = []
    for record in records:
        source = Path(args.upstream_dir) / record['upstreamFilename']
        if 'sha512-' + base64.b64encode(hashlib.sha512(source.read_bytes()).digest()).decode() != record['upstreamIntegrity']:
            raise ValueError('Upstream SHA512 integrity mismatch')
        if sha(source.read_bytes()) != record['upstreamSHA256']:
            raise ValueError('Upstream archive identity mismatch: ' + record['id'])
        with tempfile.TemporaryDirectory(prefix='vox-owned-candidate-') as temp:
            work = Path(temp)
            with tarfile.open(source) as archive:
                for member in archive:
                    parts = PurePosixPath(member.name).parts
                    if not parts or parts[0] != 'package' or '..' in parts or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe archive member')
                archive.extractall(work, filter='data')
            package = work / 'package'
            expected_diff = []
            for change in record['changes']:
                parts = PurePosixPath(change['path']).parts
                if '..' in parts or PurePosixPath(change['path']).is_absolute():
                    raise ValueError('Unsafe patch path')
                target = package / change['path']
                before_bytes = target.read_bytes() if target.exists() else b''
                actual = sha(before_bytes) if target.exists() else None
                after_text = change['afterText'] or ''
                expected_diff.extend(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in difflib.unified_diff(before_bytes.decode().splitlines(True), after_text.splitlines(True), fromfile=('a/' + change['path']) if change['beforeSHA256'] else '/dev/null', tofile=('b/' + change['path']) if change['afterText'] is not None else '/dev/null'))
                if actual != change['beforeSHA256']:
                    raise ValueError('Patch input mismatch: ' + change['path'])
                if change['afterText'] is None:
                    target.unlink()
                else:
                    data = change['afterText'].encode()
                    if sha(data) != change['afterSHA256']:
                        raise ValueError('Patch output mismatch')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            if (repo / ('security/mobile-candidate/' + record['id'] + '.patch')).read_text() != ''.join(expected_diff):
                raise ValueError('Review patch differs from pinned source changes: ' + record['id'])
            if record['id'] == 'cli':
                for module in (package / 'build').rglob('*.js'):
                    if 'node-forge' in module.read_text():
                        raise ValueError('Unmigrated CLI Forge consumer: ' + str(module.relative_to(package)))
            empty_config = work / 'empty.npmrc'
            empty_config.write_text('')
            global_config = work / 'global-empty.npmrc'
            global_config.write_text('')
            pack_env = {key: value for key, value in os.environ.items() if not key.lower().startswith('npm_config_')}
            pack_env['HOME'] = str(work)
            run = subprocess.run(['npm', 'pack', str(package), '--ignore-scripts', '--json', '--userconfig', str(empty_config), '--globalconfig', str(global_config), '--pack-destination', str(output)], cwd=work, env=pack_env, capture_output=True, text=True, timeout=60)
            if run.returncode:
                raise RuntimeError(run.stderr)
            packed = json.loads(run.stdout)[0]
            artifact = output / packed['filename']
            identity = expected[record['id']]
            native_integrity = 'sha512-' + base64.b64encode(hashlib.sha512(artifact.read_bytes()).digest()).decode()
            if packed['integrity'] != native_integrity:
                raise ValueError('npm pack native integrity mismatch')
            native_sha = sha(artifact.read_bytes())
            reference = repo / 'security/mobile-candidate/delivery/artifacts' / identity['filename']
            normalized = canonicalize_compression(artifact, reference, identity)
            canonical_integrity = 'sha512-' + base64.b64encode(hashlib.sha512(artifact.read_bytes()).digest()).decode()
            if packed['filename'] != identity['filename'] or canonical_integrity != identity['integrity'] or sha(artifact.read_bytes()) != identity['sha256']:
                reference = repo / 'security/mobile-candidate/delivery/artifacts' / identity['filename']
                diagnostic = {'id': record['id'], 'platform': platform.platform(),
                              'actualSHA256': sha(artifact.read_bytes()), 'expectedSHA256': identity['sha256'],
                              'actualTarSHA256': sha(gzip.decompress(artifact.read_bytes())),
                              'referenceTarSHA256': sha(gzip.decompress(reference.read_bytes())),
                              'nodeRuntime': json.loads(subprocess.check_output(['node', '-p', 'JSON.stringify(process.versions)'], text=True))}
                def contents(file):
                    with tarfile.open(file) as packed_archive:
                        return {m.name: {'sha256': sha(packed_archive.extractfile(m).read()),
                                         'mode': m.mode, 'mtime': m.mtime, 'size': m.size,
                                         'uid': m.uid, 'gid': m.gid, 'pax': m.pax_headers}
                                for m in packed_archive if m.isfile()}
                actual_files, reference_files = contents(artifact), contents(reference)
                diagnostic['fileDifferences'] = {name: {'actual': actual_files.get(name), 'reference': reference_files.get(name)}
                                                 for name in sorted(actual_files.keys() | reference_files.keys())
                                                 if actual_files.get(name) != reference_files.get(name)}
                print('Frozen artifact diagnostic: ' + json.dumps(diagnostic, sort_keys=True), file=__import__('sys').stderr)
                raise ValueError('Frozen artifact identity mismatch; review tool versions and unpacked source before updating pins')
            receipts.append({'id': record['id'], 'nativePack': packed, 'nativeSHA256': native_sha,
                             'canonicalIntegrity': canonical_integrity, 'sha256': sha(artifact.read_bytes()),
                             'compressionNormalizedAfterExactTarEquality': normalized, 'toolVersions': versions})
    (output / 'build-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    print('Built', len(receipts), 'private candidates; production adoption NOT qualified')


if __name__ == '__main__':
    build()
