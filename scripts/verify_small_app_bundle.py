"""Reject loose ASR weights in a standard app; never remove bundled files."""
import argparse
import os
from pathlib import Path


def verify(app: Path, *, require_qwen_runtime: bool = False) -> None:
    if not app.is_dir() or app.is_symlink():
        raise ValueError('Expected a real app bundle directory')
    if not (app / 'Contents/MacOS/VoxLog').is_file():
        raise ValueError('App executable missing')
    if os.path.lexists(app / 'Contents/Resources/Models'):
        raise ValueError('Standard app must not contain bundled Models')
    for parent, directories, files in os.walk(app, followlinks=False):
        for name in directories + files:
            if Path(name).suffix.lower() in {'.safetensors', '.gguf'}:
                raise ValueError('Standard app must not contain loose model weights')
            path = Path(parent) / name
            if path.is_symlink() and name.lower() in {'models', 'qwen3-asr'}:
                raise ValueError('Standard app must not link model directories')

    if require_qwen_runtime:
        def regular(relative, executable=False):
            path = app
            for part in Path(relative).parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError(f'Symlink in required runtime path: {relative}')
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'Missing or empty runtime file: {relative}')
            if executable and not os.access(path, os.X_OK):
                raise ValueError(f'Runtime is not executable: {relative}')

        regular('Contents/MacOS/qwen3-asr-server', executable=True)
        regular('Contents/Resources/QWEN_MODEL_CATALOG.json')
        regular('Contents/Resources/QWEN_RUNTIME_VERSIONS.txt')
        licenses = app / 'Contents/Resources/QwenRuntimeLicenses'
        if licenses.is_symlink() or not licenses.is_dir():
            raise ValueError('Missing QwenRuntimeLicenses directory')
        files = [path for path in licenses.rglob('*') if path.is_file()]
        if not files:
            raise ValueError('Empty QwenRuntimeLicenses directory')
        for path in files:
            regular(path.relative_to(app))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--require-qwen-runtime', action='store_true')
    args = parser.parse_args()
    verify(args.app, require_qwen_runtime=args.require_qwen_runtime)
    print('PASS: standard app weight boundary and requested runtime checks')
