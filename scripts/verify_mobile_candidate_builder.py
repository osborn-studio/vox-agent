"""Exercise the real candidate builder from an owned copy, including input mutations."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tarfile
import hashlib
import base64

parser = argparse.ArgumentParser()
parser.add_argument('--upstream-dir', required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parent.parent
upstream = Path(args.upstream_dir).resolve()
with tempfile.TemporaryDirectory(prefix='owned-builder-probes-', dir=repo / 'build') as tmp:
    root = Path(tmp)
    (root / 'scripts').mkdir()
    shutil.copyfile(repo / 'scripts/build_mobile_security_candidates.py', root / 'scripts/build_mobile_security_candidates.py')
    shutil.copytree(repo / 'security/mobile-candidate', root / 'security/mobile-candidate')
    def run(label, materials=upstream):
        return subprocess.run(['python3', str(root / 'scripts/build_mobile_security_candidates.py'), '--upstream-dir', str(materials), '--output-dir', str(root / label)], capture_output=True, text=True, timeout=90)
    baseline = run('baseline')
    assert baseline.returncode == 0, baseline.stderr
    rebuilt = json.loads((root / 'baseline/build-receipts.json').read_text())
    print('Exact tar-payload compression receipts: ' + json.dumps([{key: row[key] for key in ['id', 'nativeSHA256', 'sha256', 'compressionNormalizedAfterExactTarEquality']} for row in rebuilt]))
    toolchain = root / 'security/mobile-candidate/toolchain.json'
    original_toolchain = toolchain.read_bytes()
    changed_toolchain = json.loads(original_toolchain)
    changed_toolchain['node'] = 'unexpected-version'
    toolchain.write_text(json.dumps(changed_toolchain))
    invalid = run('bad-toolchain')
    assert invalid.returncode == 1 and 'Candidate build toolchain mismatch' in invalid.stderr
    toolchain.write_bytes(original_toolchain)
    records = json.loads((root / 'security/mobile-candidate/source-patches.json').read_text())
    for record in records:
        applied = root / ('git-applied-' + record['id'])
        applied.mkdir()
        with tarfile.open(upstream / record['upstreamFilename']) as archive_file:
            archive_file.extractall(applied, filter='data')
        package = applied / 'package'
        review_patch = root / 'security/mobile-candidate' / (record['id'] + '.patch')
        for command in [['git', 'apply', '--check', str(review_patch)], ['git', 'apply', str(review_patch)]]:
            checked = subprocess.run(command, cwd=package, capture_output=True, text=True, timeout=30)
            assert checked.returncode == 0, checked.stderr
        for change in record['changes']:
            changed_file = package / change['path']
            actual_hash = hashlib.sha256(changed_file.read_bytes()).hexdigest() if changed_file.exists() else None
            assert actual_hash == change['afterSHA256'], change['path']
    record_file = root / 'security/mobile-candidate/source-patches.json'
    original_records = record_file.read_bytes()
    for label, field, value, message in [
        ('before-hash', 'beforeSHA256', '0' * 64, 'Patch input mismatch'),
        ('after-hash', 'afterSHA256', '0' * 64, 'Patch output mismatch'),
        ('unsafe-path', 'path', '../outside', 'Unsafe patch path'),
    ]:
        mutated = json.loads(original_records)
        mutated[0]['changes'][0][field] = value
        record_file.write_text(json.dumps(mutated))
        rejected = run(label)
        assert rejected.returncode == 1 and message in rejected.stderr, rejected.stderr
        record_file.write_bytes(original_records)
    for label, member_name, symlink in [('unsafe-traversal', 'package/../outside', False), ('unsafe-symlink', 'package/link', True)]:
        materials = root / label
        shutil.copytree(upstream, materials)
        archive_path = materials / records[0]['upstreamFilename']
        with tarfile.open(archive_path, 'w:gz') as unsafe_archive:
            member = tarfile.TarInfo(member_name)
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = '../outside'
            unsafe_archive.addfile(member)
        mutated = json.loads(original_records)
        data = archive_path.read_bytes()
        mutated[0]['upstreamSHA256'] = hashlib.sha256(data).hexdigest()
        mutated[0]['upstreamIntegrity'] = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
        record_file.write_text(json.dumps(mutated))
        rejected = run(label + '-output', materials)
        assert rejected.returncode == 1 and 'Unsafe archive member' in rejected.stderr, rejected.stderr
        record_file.write_bytes(original_records)
    archive = records[0]['upstreamFilename']
    shutil.copytree(upstream, root / 'mutated-upstream')
    target = root / 'mutated-upstream' / archive
    target.write_bytes(target.read_bytes() + b'mutation')
    invalid = run('bad-archive', root / 'mutated-upstream')
    assert invalid.returncode == 1 and 'Upstream SHA512 integrity mismatch' in invalid.stderr
    patch = root / 'security/mobile-candidate' / (records[0]['id'] + '.patch')
    original = patch.read_bytes()
    patch.write_bytes(original + b'changed display patch\n')
    invalid = run('bad-review-patch')
    assert invalid.returncode == 1 and 'Review patch differs from pinned source changes' in invalid.stderr
    patch.write_bytes(original)
    pins = root / 'security/mobile-candidate/expected-artifacts.json'
    expected = json.loads(pins.read_text())
    expected[records[0]['id']]['sha256'] = '0' * 64
    pins.write_text(json.dumps(expected))
    invalid = run('bad-artifact-pin')
    assert invalid.returncode == 1 and 'Frozen artifact identity mismatch' in invalid.stderr
    print('PASS real builder baseline and SHA512/review-patch/artifact-pin mutation rejection')
