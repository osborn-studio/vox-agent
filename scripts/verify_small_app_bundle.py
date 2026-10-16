"""Reject loose ASR weights in a standard app; never remove bundled files."""
import argparse
import os
from pathlib import Path


def verify(app: Path) -> None:
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    verify(parser.parse_args().app)
    print('PASS: standard app has no known loose ASR weight files or Models directory')
