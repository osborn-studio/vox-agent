"""Protect the intentional AGENTS-to-CLAUDE instruction authority."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agents_bootstraps_non_claude_agents_into_claude_instructions():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "full instructions live in `CLAUDE.md`" in agents
    assert re.search(
        r"open\s+`CLAUDE\.md` \(and any files it references\)",
        agents,
    )


def test_claude_records_intentional_asymmetry_and_design_contract():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(claude.split())

    assert "## Instruction authority" in claude
    assert "deliberately not a byte-for-byte mirror" in normalized
    assert "read [DESIGN.md](DESIGN.md) in full" in normalized
    assert readme.startswith("# Vox Agent\n")
    assert "VoxLog V3" not in readme
    assert "不是换名版录音工具" in readme
