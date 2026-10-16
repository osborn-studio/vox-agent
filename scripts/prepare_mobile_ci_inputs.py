#!/usr/bin/env python3
"""Materialize pinned offline CI inputs without downloading or executing them."""
import argparse
import hashlib
import io
import json
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    table = (ROOT / 'security/mobile-candidate/safety-verifiers.json').read_bytes()
    if hashlib.sha256(table).hexdigest() != args.table_sha256:
        raise ValueError('CI input table pin mismatch')
    pins = json.loads(table)
    inputs = ROOT / 'security/mobile-candidate/ci-inputs'
    output = pathlib.Path(args.output).resolve()
    build = (ROOT / 'build').resolve()
    if not output.is_relative_to(build) or output == build or output.exists():
        raise ValueError('New owned build output required')
    captured = {}
    for file in inputs.rglob('*'):
        if file.is_symlink():
            raise ValueError('Symlink CI input')
        if file.is_dir():
            continue
        name = str(file.relative_to(ROOT))
        data = file.read_bytes()
        if name not in pins or hashlib.sha256(data).hexdigest() != pins[name]:
            raise ValueError('CI input source pin mismatch: ' + name)
        captured[str(file.relative_to(inputs))] = data
    # Validate all archive members before materialization; never extract links.
    oracle_files = {}
    with tarfile.open(fileobj=io.BytesIO(captured['bounded-matcher-oracle.tgz']), mode='r:gz') as archive:
        for member in archive:
            rel = pathlib.PurePosixPath(member.name)
            if not member.isfile() or rel.is_absolute() or '..' in rel.parts or not str(rel).startswith('node_modules/'):
                raise ValueError('Unsafe reference archive member')
            if member.name in oracle_files or member.size > 2_000_000:
                raise ValueError('Reference archive limit')
            oracle_files[member.name] = archive.extractfile(member).read()
    if sum(map(len, oracle_files.values())) > 4_000_000:
        raise ValueError('Reference archive total limit')
    output.mkdir(parents=True)
    for name, data in captured.items():
        if name == 'bounded-matcher-oracle.tgz':
            continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    oracle = output / 'oracle/mobile/voxagent-companion'
    oracle.mkdir(parents=True)
    (oracle / 'package.json').write_text('{"private":true,"description":"bounded semantic reference; no installation or attacks"}\n')
    for name, data in oracle_files.items():
        target = oracle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(json.dumps({'output': str(output), 'oracleRoot': str(oracle), 'offline': True, 'oracleFiles': len(oracle_files)}))


if __name__ == '__main__':
    main()
