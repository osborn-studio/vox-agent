"""Tests for engine.config_validator — pre-flight configuration checks."""

import pytest
from engine.config_validator import ConfigValidator


@pytest.fixture
def validator(tmp_path):
    return ConfigValidator(
        data_dir=tmp_path / "data",
        agents_dir=tmp_path / "agents",
        skills_dir=tmp_path / "skills",
    )


def test_default_directories_use_vox_agent_namespace(monkeypatch, tmp_path):
    canonical = tmp_path / "Vox Agent"
    monkeypatch.setenv("VOXAGENT_DATA_DIR", str(canonical))

    default_validator = ConfigValidator()

    assert default_validator.data_dir == canonical
    assert default_validator.skills_dir == canonical / "skills"


class TestConfigValidator:
    def test_valid_empty(self, validator):
        result = validator.validate()
        assert result.valid

    def test_creates_data_dir(self, validator, tmp_path):
        result = validator.validate()
        assert (tmp_path / "data").exists()

    def test_with_agents(self, validator, tmp_path):
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "claude").mkdir()
        (agents / "claude" / "identity.md").write_text("---\nid: claude\n---\n")
        result = validator.validate()
        assert result.valid
        agent_check = next(c for c in result.checks if c["name"] == "agents_dir")
        assert "1 agents" in agent_check["detail"]

    def test_missing_agents_warning(self, validator):
        result = validator.validate()
        assert any("agents" in w.lower() for w in result.warnings)

    def test_with_skills(self, validator, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "review.md").write_text("# Review")
        result = validator.validate()
        skill_check = next(c for c in result.checks if c["name"] == "skills_dir")
        assert "1 skills" in skill_check["detail"]

    def test_python_deps_present(self, validator):
        result = validator.validate()
        dep_check = next(c for c in result.checks if c["name"] == "python_deps")
        assert dep_check["passed"]

    def test_cli_tools_check(self, validator):
        result = validator.validate()
        cli_check = next(c for c in result.checks if c["name"] == "cli_tools")
        assert cli_check["passed"]  # always passes (just warns)

    def test_all_checks_present(self, validator):
        result = validator.validate()
        check_names = {c["name"] for c in result.checks}
        assert "data_dir" in check_names
        assert "agents_dir" in check_names
        assert "skills_dir" in check_names
        assert "python_deps" in check_names
        assert "cli_tools" in check_names
