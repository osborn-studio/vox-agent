"""Build metadata identity generation, no App execution or signing."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from write_app_model_catalog import write_catalog  # noqa: E402


def test_compiled_literal_identity_is_create_only(tmp_path):
    app = tmp_path / 'App.app'
    (app / 'Contents/Resources').mkdir(parents=True)
    source = tmp_path / 'catalog.swift'
    source.write_text('static let modelID = "model"\nstatic let revision = "revision"\nstatic let digest = "digest"\n')
    assert write_catalog(source, app) == {'model_id': 'model', 'revision': 'revision', 'digest': 'digest'}
    before = (app / 'Contents/Resources/QWEN_MODEL_CATALOG.json').read_bytes()
    with pytest.raises(FileExistsError):
        write_catalog(source, app)
    assert (app / 'Contents/Resources/QWEN_MODEL_CATALOG.json').read_bytes() == before


@pytest.mark.parametrize('text', ['', 'static let modelID = "one"\nstatic let modelID = "two"'])
def test_missing_or_ambiguous_literals_create_nothing(tmp_path, text):
    app = tmp_path / 'App.app'
    (app / 'Contents/Resources').mkdir(parents=True)
    source = tmp_path / 'catalog.swift'
    source.write_text(text)
    with pytest.raises(ValueError):
        write_catalog(source, app)
    assert not (app / 'Contents/Resources/QWEN_MODEL_CATALOG.json').exists()
