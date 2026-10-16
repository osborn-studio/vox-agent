#!/usr/bin/env python3
"""Prepare a new owned target fixture from one fixed committed tree; never install."""
import argparse
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE_PIN = '8c1267ce8bc55d27b42ee7fd29fb68c929f0807cc670a32a93f67399b1d7340f'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--legacy-dir', required=True, type=pathlib.Path)
    args = parser.parse_args()
    output = pathlib.Path(args.output).resolve()
    build = (ROOT / 'build').resolve()
    if not output.is_relative_to(build) or output == build or output.exists():
        parser.error('Output must be a new directory strictly beneath repository build')
    source_ref = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    tree = subprocess.check_output(['git', 'ls-tree', '-rz', source_ref, '--',
                                    'mobile/voxagent-companion', 'scripts', 'security'], cwd=ROOT)
    blobs = {}
    for item in tree.split(b'\0'):
        if not item:
            continue
        metadata, name = item.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('Only ordinary committed files permitted: ' + name.decode())
        blobs[name.decode()] = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()], cwd=ROOT)
    pin_bytes = blobs['security/mobile-candidate/safety-verifiers.json']
    if hashlib.sha256(pin_bytes).hexdigest() != TABLE_PIN:
        raise ValueError('Safety verifier table pin mismatch')
    for name, expected in json.loads(pin_bytes).items():
        if hashlib.sha256(blobs[name]).hexdigest() != expected:
            raise ValueError('Committed source pin mismatch: ' + name)
    legacy = []
    for entry in json.loads(blobs['security/mobile-dependency-patches.json'])['patches']:
        relative = pathlib.Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe legacy fixture path')
        data = (args.legacy_dir / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['after']:
            raise ValueError('Legacy fixture hash mismatch: ' + str(relative))
        legacy.append((relative, data))
    # Read and validate all inputs before creating anything. Exactly these blobs
    # are then written: dirty files and a later HEAD change cannot mix snapshots.
    output.mkdir(parents=True)
    for relative, data in legacy:
        target = output / 'security/legacy-defense-fixtures' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name, data in blobs.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    app = output / 'mobile/voxagent-companion'
    for source, target in [('package.template.json', 'package.json'), ('package-lock.template.json', 'package-lock.json')]:
        (app / target).write_bytes(blobs['security/mobile-candidate/delivery/' + source])
    patch = output / 'security/mobile-candidate/legacy-oracle-dispatch.patch'
    subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(patch)], cwd=output, check=True)
    installer = output / 'scripts/verify_mobile_patch_installer.cjs'
    old = "path.join(repo, 'mobile/voxagent-companion/node_modules', patch.path)"
    new = "path.join(repo, 'security/legacy-defense-fixtures', patch.path)"
    content = installer.read_text()
    if content.count(old) != 1:
        raise ValueError('Legacy fixture installer anchor mismatch')
    installer.write_text(content.replace(old, new))
    for name, expected in json.loads(blobs['security/mobile-candidate/fixture-verifiers.json']).items():
        if hashlib.sha256((output / 'scripts' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Prepared helper pin mismatch: ' + name)
    print(json.dumps({'fixture': str(output), 'appRoot': str(app), 'sourceRef': source_ref,
                      'trackedFilesCopied': len(blobs), 'installed': False}))


if __name__ == '__main__':
    main()
