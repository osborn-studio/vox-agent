"""Read-only payload comparison for online/offline App copies; no signing claim."""
import argparse
import hashlib
import os
from pathlib import Path
import stat


def scan_error(error):
    raise error


def inventory(app: Path):
    if app.is_symlink() or not app.is_dir():
        raise ValueError('Expected ordinary App root')
    app = app.resolve()
    if not (app / 'Contents/MacOS/VoxLog').is_file():
        raise ValueError('Missing executable')
    entries = {'.': ('directory', stat.S_IMODE(app.stat().st_mode))}
    for parent, directories, files in os.walk(app, followlinks=False, onerror=scan_error):
        for name in sorted(directories + files):
            path = Path(parent) / name
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            relative = path.relative_to(app).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve().is_relative_to(app):
                    raise ValueError('App link escapes bundle')
                entries[relative] = ('link', mode, target)
            elif stat.S_ISDIR(info.st_mode):
                entries[relative] = ('directory', mode)
            elif stat.S_ISREG(info.st_mode):
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, 'rb') as incoming:
                    opened = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        raise ValueError('App file changed')
                    digest = hashlib.file_digest(incoming, 'sha256').hexdigest()
                    entries[relative] = ('file', stat.S_IMODE(opened.st_mode), opened.st_size, digest)
            else:
                raise ValueError('Unsupported App entry')
    return entries


def verify(online: Path, offline: Path):
    if online.samefile(offline):
        raise ValueError('Compare independent App copies')
    if inventory(online) != inventory(offline):
        raise ValueError('Online and offline App payloads differ')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online-app', required=True, type=Path)
    parser.add_argument('--offline-app', required=True, type=Path)
    args = parser.parse_args()
    verify(args.online_app, args.offline_app)
    print('PASS: App paths, entry kinds, modes, file bytes and internal link targets match; signing and xattrs not verified')
