import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "macos_image_verify", Path(__file__).resolve().parents[2] / "scripts/ci/macos_image_verify.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def image(tmp_path):
    disk = b'first-layer' + b'second-layer'
    nvram = b'synthetic-nvram'
    def digest(value):
        return 'sha256:' + hashlib.sha256(value).hexdigest()
    layers = [{'mediaType': 'application/vnd.cirruslabs.tart.disk.v2',
               'annotations': {'org.cirruslabs.tart.uncompressed-size': str(len(b)),
                               'org.cirruslabs.tart.uncompressed-content-digest': digest(b)}}
              for b in (b'first-layer', b'second-layer')]
    layers.append({'mediaType': 'application/vnd.cirruslabs.tart.nvram.v1',
                   'size': len(nvram), 'digest': digest(nvram)})
    raw = json.dumps({'layers': layers}).encode()
    manifest = tmp_path / 'manifest.json'
    manifest.write_bytes(raw)
    (tmp_path / 'disk.img').write_bytes(disk)
    (tmp_path / 'nvram.bin').write_bytes(nvram)
    return manifest, tmp_path, hashlib.sha256(raw).hexdigest()


def test_verifies_actual_disk_ranges_and_nvram(image):
    result = m.verify(*image)
    assert result['disk_layers_verified'] == 2
    assert result['uncompressed_disk_bytes'] == 23


@pytest.mark.parametrize('file,content', [('disk.img', b'tampered'),
                                        ('disk.img', b'first-layersecond-layertrailing'),
                                        ('nvram.bin', b'tampered'),
                                        ('nvram.bin', b'synthetic-nvramtrailing')])
def test_changed_bytes_and_trailing_data_rejected(image, file, content):
    manifest, directory, pin = image
    (directory / file).write_bytes(content)
    with pytest.raises(ValueError):
        m.verify(manifest, directory, pin)


def test_manifest_pin_and_symlink_rejected(image):
    manifest, directory, pin = image
    with pytest.raises(ValueError):
        m.verify(manifest, directory, '0' * 64)
    (directory / 'alias').symlink_to(directory / 'disk.img')
    (directory / 'disk.img').unlink()
    (directory / 'disk.img').symlink_to(directory / 'alias')
    with pytest.raises(ValueError):
        m.verify(manifest, directory, pin)
