"""Build an isolated native GUI from the production ASR status view; does not launch."""

import argparse
from pathlib import Path
import plistlib
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
app = root / "build/gui-acceptance/Vox GUI Acceptance.app"
contents = app / "Contents"
bin_dir = contents / "MacOS"
bin_dir.mkdir(parents=True, exist_ok=True)
with (contents / "Info.plist").open("wb") as stream:
    plistlib.dump(
        {
            "CFBundleIdentifier": "com.osborn.voxagent.gui-acceptance",
            "CFBundleName": "Vox GUI Acceptance",
            "CFBundleExecutable": "Fixture",
            "CFBundlePackageType": "APPL",
            "NSHighResolutionCapable": True,
        },
        stream,
    )
subprocess.run(
    [
        "swiftc",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        str(root / "macos/VoxLog/UI/DesignSystem.swift"),
        str(root / "macos/VoxLog/UI/Settings/LocalASRStatusView.swift"),
        str(root / "tests/fixtures/gui/LocalASRFixture.swift"),
        "-o",
        str(bin_dir / "Fixture"),
    ],
    check=True,
    timeout=120,
)
print(app)
