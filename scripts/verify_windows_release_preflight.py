"""Read-only Windows release preflight; never claims device or parity acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import tomllib
from pathlib import Path

DESKTOP_PACKAGE = 'vox-agent-desktop-tauri'


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_toml(path: Path):
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def cargo_toml_version(path: Path):
    """Literal [package].version; workspace-inherited versions are unsupported (None)."""
    package = (_read_toml(path) or {}).get('package')
    version = package.get('version') if isinstance(package, dict) else None
    return version if isinstance(version, str) else None


def cargo_lock_version(path: Path, name: str = DESKTOP_PACKAGE):
    """Version of the single lock entry named `name`; None if absent or duplicated."""
    packages = (_read_toml(path) or {}).get('package')
    if not isinstance(packages, list):
        return None
    entries = [p for p in packages if isinstance(p, dict) and p.get('name') == name]
    if len(entries) != 1 or not isinstance(entries[0].get('version'), str):
        return None
    return entries[0]['version']


def npm_lock_version(path: Path, package_path: str = ''):
    """Version recorded for `package_path`. For the package's own lock ('' path)
    the top-level version and packages[''] must agree, else None."""
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    packages = data.get('packages')
    nested = packages.get(package_path, {}).get('version') if isinstance(packages, dict) and isinstance(packages.get(package_path), dict) else None
    if package_path != '':
        return nested
    top = data.get('version')
    if nested is not None and top is not None and nested != top:
        return None
    return nested if nested is not None else top


STAGES = ('source', 'host', 'sku')
SKUS = ('preview', 'full')
WINDOWS_TRIPLE = 'x86_64-pc-windows-msvc'
PE_MACHINES = {0x8664: 'x64', 0xAA64: 'arm64', 0x014C: 'x86'}
_GLOB_CHARS = set('*?[')


def _pe_machine(data: bytes):
    """Machine name for a structurally complete PE image, else None.

    Requires the DOS header, `PE\\0\\0` signature, full COFF header, an optional
    header of the declared size whose magic matches the machine (PE32+ for
    x64/arm64, PE32 for x86), and a section table that fits in the file.
    """
    if len(data) < 0x40 or data[:2] != b'MZ':
        return None
    offset = int.from_bytes(data[0x3C:0x40], 'little')
    coff = offset + 4
    if offset > len(data) or data[offset:coff] != b'PE\0\0' or len(data) < coff + 20:
        return None
    machine = int.from_bytes(data[coff:coff + 2], 'little')
    sections = int.from_bytes(data[coff + 2:coff + 4], 'little')
    optional_size = int.from_bytes(data[coff + 16:coff + 18], 'little')
    optional = coff + 20
    if sections == 0 or optional_size < 2 or len(data) < optional + optional_size + 40 * sections:
        return None
    magic = int.from_bytes(data[optional:optional + 2], 'little')
    expected_magic = 0x10B if machine == 0x014C else 0x20B
    if magic != expected_magic:
        return None
    return PE_MACHINES.get(machine, f'0x{machine:04x}')


def _resolve_pattern(tauri: Path, repo: Path, pattern: str):
    """Expand a Tauri resource pattern relative to src-tauri.

    Returns (files, outside_repository). Directories (named directly or matched
    by a glob such as `dir/**`) expand to every file beneath them. Every result
    is resolved through symlinks; anything resolving outside the repository is
    reported, never silently dropped.
    """
    root = repo.resolve()
    parts = Path(pattern).parts
    split = next((i for i, part in enumerate(parts) if _GLOB_CHARS & set(part)), len(parts))
    base = (tauri.joinpath(*parts[:split]) if split else tauri).resolve()
    if not base.is_relative_to(root):
        return [], True
    matches = [base] if split == len(parts) else list(base.glob(str(Path(*parts[split:]))))
    files = set()
    for match in matches:
        candidates = [match] if not match.is_dir() else [p for p in match.rglob('*')]
        for candidate in candidates:
            if candidate.is_dir() and not candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                return [], True
            if resolved.is_file():
                files.add(resolved)
    return sorted(files), False


def _artifact(tauri: Path, path: Path, blockers: list):
    data = path.read_bytes()
    rel = Path(os.path.relpath(path, tauri)).as_posix()
    item = {'path': rel, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if path.suffix.lower() in {'.exe', '.dll'}:
        machine = _pe_machine(data)
        if machine is None:
            blockers.append(f'bundled_file_not_pe:{rel}')
        elif machine != 'x64':
            blockers.append(f'bundled_file_wrong_architecture:{rel}')
        item['machine'] = machine
    return item


ENGINE_BIN = 'voxagent-engine'
RELEASE_CONFIG = 'tauri.release-engine.conf.json'


def _engine_manifest(path: Path, artifact: dict) -> list:
    """The frozen engine must carry the manifest build_windows_engine.py wrote,
    naming this exact file, target and digest (T02/T29)."""
    manifest = path.with_name(f'{ENGINE_BIN}.manifest.json')
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [f'bundled_engine_manifest_missing:{path.name}']
    if not (isinstance(data, dict) and data.get('file') == path.name
            and data.get('target') == WINDOWS_TRIPLE and data.get('sha256') == artifact.get('sha256')):
        return [f'bundled_engine_manifest_mismatch:{path.name}']
    return []


def _with_release_config(tauri: Path, bundle: dict):
    """Merge the externalBin of the release config that `tauri build --config`
    adds, so the sku stage checks what packaging will actually bundle."""
    path = tauri / RELEASE_CONFIG
    if not path.exists():
        return bundle, None, []
    try:
        extra = json.loads(path.read_text(encoding='utf-8')).get('bundle', {}).get('externalBin', [])
    except (OSError, ValueError, AttributeError):
        return bundle, RELEASE_CONFIG, ['release_config_unreadable']
    if not isinstance(extra, list):
        return bundle, RELEASE_CONFIG, ['release_config_unreadable']
    # Tauri applies --config as a JSON merge patch: the array replaces the base.
    merged = dict(bundle)
    merged['externalBin'] = extra
    return merged, RELEASE_CONFIG, []


def check_sku(repo: Path, tauri: Path, bundle: dict, sku: str):
    """Packaging checks. Every declared file must exist (and PE images must be
    x64); the full SKU additionally requires a bundled runtime declaration."""
    tauri = tauri.resolve()  # match resolved file paths (e.g. /var -> /private/var)
    blockers, artifacts = [], []
    external = bundle.get('externalBin') or []
    resources = bundle.get('resources') or []
    if isinstance(resources, dict):
        # Map form {source: destination}: destinations must be relative paths
        # inside the bundle, never absolute or escaping with "..".
        if not all(isinstance(dest, str) and dest and not Path(dest).is_absolute()
                   and '..' not in Path(dest).parts for dest in resources.values()):
            return ['invalid_bundle_declaration'], []
        resources = list(resources)
    if not isinstance(external, list) or not isinstance(resources, list) or not all(
            isinstance(x, str) for x in [*external, *resources]):
        return ['invalid_bundle_declaration'], []
    if sku == 'full' and not (external or resources):
        blockers.append('bundled_runtime_not_declared')
    for entry in external:
        expected = f'{entry}-{WINDOWS_TRIPLE}.exe'
        path = (tauri / expected).resolve()
        if not path.is_relative_to(repo.resolve()):
            blockers.append(f'bundled_path_outside_repository:{entry}')
        elif not path.is_file():
            blockers.append(f'bundled_file_missing:{expected}')
        else:
            artifact = _artifact(tauri, path, blockers)
            artifacts.append(artifact)
            if Path(entry).name == ENGINE_BIN:
                blockers.extend(_engine_manifest(path, artifact))
    for pattern in resources:
        files, outside = _resolve_pattern(tauri, repo, pattern)
        if outside:
            blockers.append(f'bundled_path_outside_repository:{pattern}')
        elif not files:
            blockers.append(f'bundled_resource_unmatched:{pattern}')
        artifacts.extend(_artifact(tauri, path, blockers) for path in files)
    return blockers, artifacts


def qualify(repo: Path, version: str, host: str, sku: str = 'full', stages=STAGES) -> dict:
    """Run the requested preflight stages.

    source: versions and bundle configuration (runs on any host).
    host:   the build host is Windows.
    sku:    packaging declarations for `preview` (engine optional) or `full`.
    Passing stages never upgrades installer, signing, device or parity status.
    """
    if sku not in SKUS or not stages or any(stage not in STAGES for stage in stages):
        raise ValueError('unknown sku or stage')
    shell = repo / 'apps/desktop-tauri'
    stage_status = {stage: 'NOT_RUN' for stage in STAGES}
    try:
        package = json.loads((shell / 'package.json').read_text(encoding='utf-8'))
        config = json.loads((shell / 'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return {'status': 'BLOCKED', 'blockers': ['configuration_unreadable'], 'sku': sku,
                'stages': stage_status, 'error_type': type(exc).__name__, 'release_acceptance': 'NOT_RUN'}
    bundle = config.get('bundle', {})
    versions = {
        'npm': package.get('version'), 'tauri': config.get('version'),
        'cargo': cargo_toml_version(shell / 'src-tauri/Cargo.toml'),
        'cargo_lock': cargo_lock_version(shell / 'src-tauri/Cargo.lock'),
        'npm_lock': npm_lock_version(shell / 'package-lock.json'),
        'root_npm_lock': npm_lock_version(repo / 'package-lock.json', 'apps/desktop-tauri'),
    }
    blockers, artifacts = [], []
    if 'source' in stages:
        found = [f'{component}_version_mismatch' for component, current in versions.items() if current != version]
        if not isinstance(bundle, dict):
            found.append('invalid_bundle_configuration')
        elif bundle.get('active') is not True:
            found.append('bundling_not_enabled')
        stage_status['source'] = 'BLOCKED' if found else 'PASS'
        blockers += found
    if 'host' in stages:
        found = [] if host == 'Windows' else ['windows_host_required']
        stage_status['host'] = 'BLOCKED' if found else 'PASS'
        blockers += found
    release_config = None
    if 'sku' in stages:
        if isinstance(bundle, dict):
            packaged, release_config, found = _with_release_config(shell / 'src-tauri', bundle)
            more, artifacts = check_sku(repo, shell / 'src-tauri', packaged, sku)
            found = found + more
            if sku == 'full':
                # The full SKU ships the engine the launcher requires (T29a).
                if release_config is None:
                    found.append('release_config_missing')
                engines = [e for e in packaged.get('externalBin') or [] if isinstance(e, str)
                           and Path(e).name == ENGINE_BIN]
                if not engines:
                    found.append('bundled_engine_not_declared')
        else:
            found, artifacts = ['invalid_bundle_configuration'], []
        stage_status['sku'] = 'BLOCKED' if found else 'PASS'
        blockers += found
    return {
        'contract': 'windows-release-preflight-v3',
        'requested_version': version, 'sku': sku, 'stages': stage_status,
        'source_versions': versions, 'windows_host': host == 'Windows',
        'bundled_artifacts': artifacts,
        'release_config': release_config,
        'status': 'BLOCKED' if blockers else 'PASS',
        'blockers': blockers,
        'limitations': ['File presence, hashes and PE architecture do not prove the runtime launches.',
                        'Preflight does not establish product feature parity.'],
        'installer_acceptance': 'NOT_RUN', 'authenticode': 'NOT_RUN',
        'microphone': 'NOT_RUN', 'upgrade_rollback': 'NOT_RUN',
        'release_acceptance': 'NOT_RUN',
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--version', required=True)
    parser.add_argument('--sku', choices=SKUS, default='full')
    parser.add_argument('--stage', action='append', choices=STAGES,
                        help='repeatable; default runs every stage')
    args = parser.parse_args(argv)
    result = qualify(args.repo, args.version, platform.system(), sku=args.sku,
                     stages=tuple(args.stage) if args.stage else STAGES)
    print(json.dumps(result, indent=2))
    return 2 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
