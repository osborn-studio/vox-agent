"""Ensure the compiled allowlist stays tied to the independently checked revision."""

import hashlib
import json
from pathlib import Path
import re


def test_catalog_matches_upstream_revision_evidence():
    root = Path(__file__).parents[1]
    evidence = json.loads(
        (root / "docs/verification/component-updates/v4.2.3-upstream-evidence.json").read_text()
    )
    source = (root / "macos/VoxLog/Services/QwenModelCatalog.swift").read_text()
    entries = sorted(
        [
            {key: row[key] for key in ("path", "size", "sha256")}
            for row in evidence["files"]
            if row["status"] == "match"
        ],
        key=lambda row: row["path"],
    )
    actual = [
        {"path": path, "size": int(size), "sha256": sha}
        for path, size, sha in re.findall(
            r'\.init\(path: "([^"]+)", size: (\d+), sha256: "([0-9a-f]+)"\)', source
        )
    ]
    assert actual == entries
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    assert f'static let digest = "{digest}"' in source
    assert f'static let revision = "{evidence["revision"]}"' in source
