"""T01c: the Context / three-pane baseline subset table covers every fact
extracted from the pinned macOS source, and says it is only a subset."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((ROOT / "docs/verification/windows-t01c-context-baseline.json").read_text(encoding="utf-8"))
DOC = (ROOT / "docs/verification/windows-t01c-context-baseline.md").read_text(encoding="utf-8")
STATUSES = ("PASS", "PARTIAL", "GAP", "BY DESIGN")


def rows():
    return [line for line in DOC.splitlines() if line.startswith("| ") and not line.startswith("|---")]


def row_for(cell: str) -> str:
    found = [line for line in rows() if line.split("|")[1].strip() == cell]
    assert len(found) == 1, f"exactly one row for {cell!r}"
    return found[0]


def test_every_context_api_path_has_one_row_with_a_status():
    for path in SNAPSHOT["contextApiPaths"]:
        row = row_for(f"`{path}`")
        assert any(f"| {status} |" in row for status in STATUSES), row


def test_every_sidebar_selection_and_label_is_mapped():
    for case in SNAPSHOT["sidebarSelections"]:
        row_for(f"`{case}`")
    for label in SNAPSHOT["sidebarLabels"]:
        row = row_for(label["en"])
        assert any(status in row for status in STATUSES), row


def test_the_table_names_its_pinned_source_and_does_not_claim_the_full_inventory():
    assert SNAPSHOT["sourceCommit"] in DOC
    assert SNAPSHOT["sourceRef"] in DOC
    assert "not the full macOS baseline inventory (T01)" in DOC
    for path in SNAPSHOT["engineApiPathsOutOfScope"]:
        assert f"`{path}`" in DOC


def test_pass_rows_cite_evidence_that_exists():
    evidence = ROOT / "build/windows-v4.2.8/evidence"
    if not evidence.is_dir():  # evidence is local build output
        return
    present = {p.name.split(".")[0] for p in evidence.glob("*.json")}
    for line in rows():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 4 and cells[3] == "PASS":
            for task in re.findall(r"\bT\d+[a-z]?(?:-ui)?\b", cells[4]):
                if task.startswith(("T07b", "T29", "T38", "T42")):
                    continue  # named dependencies, not this row's evidence
                assert task in present, f"{cells[1]} cites {task} without evidence"


def test_every_dynamic_path_segment_is_covered_by_an_operation_row():
    section = DOC[DOC.index("## API operations built at run time"):DOC.index("## Sidebar selections")]
    assert SNAPSHOT["dynamicPathSegments"], "the extraction found the run-time segments"
    for segment in SNAPSHOT["dynamicPathSegments"]:
        assert f"/{segment}" in section, segment


def test_multiline_labels_are_extracted():
    assert any("awaiting approval" in label["en"] for label in SNAPSHOT["sidebarLabels"])


def test_known_run_time_operations_are_listed():
    section = DOC[DOC.index("## API operations built at run time"):DOC.index("## Sidebar selections")]
    for template in (
        "v1/input-backups/{eventId}",
        "v1/conversations/{id}/messages",
        "v1/conversations/{id}/receipts/{eventId}",
        "v1/context-private/{id}",
        "v1/context-private/{id}/withdraw",
        "v1/context-private-events/{eventId}`",
        "v1/context-private-events/{eventId}/requests/{requestId}",
    ):
        assert template in section, template
