"""Create-only ZIP of CLI-verified model media; no download, install or release upload."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid
import zipfile


def private_parent(path: Path) -> None:
    if path != path.resolve():
        raise ValueError('Use physical canonical parent paths')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Parent must be private')


def walk_error(error):
    raise error


def package_directory(source: Path, output: Path) -> None:
    """Package a private staged directory. Archive hash is not model authenticity."""
    if source != source.resolve() or output.parent != output.parent.resolve():
        raise ValueError('Use physical canonical paths')
    if output.is_relative_to(source):
        raise ValueError('Archive must be outside the media directory')
    info = source.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe media directory')
    entries = []
    for parent, directories, names in os.walk(source, followlinks=False, onerror=walk_error):
        for name in sorted(directories + names):
            path = Path(parent) / name
            item = path.lstat()
            if item.st_uid != os.getuid() or item.st_mode & 0o022:
                raise ValueError('Unsafe media entry')
            if not (stat.S_ISDIR(item.st_mode) or stat.S_ISREG(item.st_mode)):
                raise ValueError('Only ordinary files and directories are supported')
            if stat.S_ISREG(item.st_mode):
                entries.append(path)
    if not entries:
        raise ValueError('Empty model media')
    private_parent(output.parent)
    if os.path.lexists(output):
        raise FileExistsError('Archive already exists')
    temporary = output.parent / ('.model-archive-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            destination_file = os.fdopen(descriptor, 'wb')
        except BaseException:
            os.close(descriptor)
            raise
        with destination_file as destination:
            with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for path in sorted(entries):
                    relative = path.relative_to(source).as_posix()
                    metadata = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    metadata.create_system = 3
                    metadata.external_attr = (stat.S_IFREG | 0o600) << 16
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as incoming:
                        opened = os.fstat(incoming.fileno())
                        metadata.file_size = opened.st_size
                        if not stat.S_ISREG(opened.st_mode):
                            raise ValueError('Media changed during packaging')
                        with archive.open(metadata, 'w', force_zip64=True) as outgoing:
                            while block := incoming.read(1024 * 1024):
                                outgoing.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary, output, follow_symlinks=False)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True, help='Locally built pinned model-import utility')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--staging-parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.suffix.lower() != '.zip' or os.path.lexists(args.output):
        parser.error('Output must be a new .zip path')
    private_parent(args.output.parent)
    private_parent(args.staging_parent)
    result = subprocess.run([str(args.cli), 'media', str(args.staging_parent), str(args.source)],
                            capture_output=True, text=True)
    if result.returncode:
        parser.exit(result.returncode, result.stderr)
    receipt = json.loads(result.stdout)
    staged = Path(receipt['verified_path'])
    if receipt['operation'] != 'media' or staged.parent != args.staging_parent:
        raise ValueError('Unexpected media receipt')
    import sys
    print('Retained verified media: ' + str(staged), file=sys.stderr)
    package_directory(staged, args.output)
    # Keep verified media even when packaging fails; operator owns its lifecycle.
    print(json.dumps({'archive': str(args.output), 'retained_media': str(staged),
                      'cli_reported_digest': receipt['digest'], 'cli_reported_revision': receipt['revision']}, sort_keys=True))


if __name__ == '__main__':
    main()
