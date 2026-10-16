"""Tests for deterministic Python tooling used locally and in CI."""

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RUFF_VERSION = "0.15.22"


def test_ruff_is_exactly_pinned_and_matches_the_ci_install():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dev_dependencies = tomllib.load(handle)["project"]["optional-dependencies"]["dev"]

    ruff_requirements = [
        requirement for requirement in dev_dependencies if requirement.startswith("ruff")
    ]
    assert ruff_requirements == [f"ruff=={EXPECTED_RUFF_VERSION}"]

    workflow = (ROOT / ".github/source-workflows/test-engine.yml.txt").read_text(encoding="utf-8")
    ci_pin = re.search(r"python -m pip install ruff==([0-9.]+)", workflow)

    assert ci_pin is not None
    assert ci_pin.group(1) == EXPECTED_RUFF_VERSION
