import json
import plistlib
import tomllib
from pathlib import Path

from apps.desktop.server import app as desktop_bridge_app
from engine import __version__


ROOT = Path(__file__).resolve().parents[1]


RELEASE_VERSION = "4.2.10"


def test_release_version_is_one_product_version_across_engine_desktop_and_app_bundle():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        package_version = tomllib.load(handle)["project"]["version"]
    with (ROOT / "macos/VoxLog/Resources/Info.plist").open("rb") as handle:
        app_info = plistlib.load(handle)
    with (ROOT / "apps/desktop-tauri/package.json").open(encoding="utf-8") as handle:
        desktop_package = json.load(handle)
    with (ROOT / "apps/desktop-tauri/package-lock.json").open(
        encoding="utf-8"
    ) as handle:
        desktop_package_lock = json.load(handle)
    with (ROOT / "package-lock.json").open(encoding="utf-8") as handle:
        root_package_lock = json.load(handle)
    with (ROOT / "apps/desktop-tauri/src-tauri/Cargo.toml").open("rb") as handle:
        desktop_cargo = tomllib.load(handle)
    with (ROOT / "apps/desktop-tauri/src-tauri/Cargo.lock").open("rb") as handle:
        desktop_cargo_lock = tomllib.load(handle)
    with (ROOT / "apps/desktop-tauri/src-tauri/tauri.conf.json").open(
        encoding="utf-8"
    ) as handle:
        desktop_config = json.load(handle)

    assert package_version == RELEASE_VERSION
    assert __version__ == package_version
    assert app_info["CFBundleShortVersionString"] == package_version
    assert app_info["CFBundleVersion"] == package_version
    assert desktop_package["version"] == package_version
    assert desktop_package_lock["packages"][""]["version"] == package_version
    assert (
        root_package_lock["packages"]["apps/desktop-tauri"]["version"]
        == package_version
    )
    assert desktop_cargo["package"]["version"] == package_version
    locked_desktop = next(
        package
        for package in desktop_cargo_lock["package"]
        if package["name"] == "vox-agent-desktop-tauri"
    )
    assert locked_desktop["version"] == package_version
    assert desktop_config["version"] == package_version
    assert desktop_bridge_app.version == package_version
    assert (ROOT / f"docs/releases/{package_version}.md").is_file()
    assert f"- Version: `{package_version}`" in (ROOT / "docs/RELEASE.md").read_text(
        encoding="utf-8"
    )
