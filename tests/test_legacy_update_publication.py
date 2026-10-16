import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "update_assets", ROOT / "scripts/verify_update_assets.py"
)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def test_valid_small_release_has_checksum_and_no_feed(tmp_path):
    dmg = tmp_path / "VoxAgent-v4.3.0-cloud.dmg"
    dmg.write_bytes(b"synthetic DMG fixture")
    assets.checksum(tmp_path, "4.3.0")
    assert (
        tmp_path / "SHA256SUMS.txt"
    ).read_text() == f"{hashlib.sha256(dmg.read_bytes()).hexdigest()}  {dmg.name}\n"
    assert not (tmp_path / assets.LEGACY_FEED).exists()


@pytest.mark.parametrize("kind", ["file", "dangling-link", "directory"])
def test_stale_legacy_feed_blocks_release_without_deleting_it(tmp_path, kind):
    legacy = tmp_path / assets.LEGACY_FEED
    if kind == "file":
        legacy.write_text("{}")
    elif kind == "directory":
        legacy.mkdir()
    else:
        legacy.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Legacy"):
        assets.verify(tmp_path, "4.3.0")
    assert legacy.exists() or legacy.is_symlink()


def test_missing_and_linked_dmg_rejected(tmp_path):
    with pytest.raises(ValueError, match="DMG"):
        assets.verify(tmp_path, "4.3.0")
    (tmp_path / "VoxAgent-v4.3.0-cloud.dmg").symlink_to(__file__)
    with pytest.raises(ValueError, match="DMG"):
        assets.verify(tmp_path, "4.3.0")


def test_invalid_version_and_symlink_checksum_rejected(tmp_path):
    with pytest.raises(ValueError, match="version"):
        assets.verify(tmp_path, "../4.3.0")
    (tmp_path / "VoxAgent-v4.3.0-cloud.dmg").write_bytes(b"dmg")
    (tmp_path / "SHA256SUMS.txt").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        assets.checksum(tmp_path, "4.3.0")
    assert not (tmp_path / "outside").exists()


def test_build_and_workflow_do_not_emit_legacy_feed():
    build = (ROOT / "scripts/build_voxagent_dmg.sh").read_text()
    workflow = (ROOT / ".github/source-workflows/release-dmg.yml.txt").read_text()
    assert 'cat > "$DIST_DIR/voxagent-updates.json"' not in build
    assert "verify_update_assets.py" in build
    assert "verify_update_assets.py" in workflow
    assert "dist/voxagent-updates.json" not in workflow
