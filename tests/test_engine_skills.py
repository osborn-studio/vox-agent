"""Tests for engine.skills — Skill loader with YAML frontmatter."""

import pytest

from engine.skills import SkillLoader


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


class TestSkillLoader:
    def test_empty_dir(self, skills_dir):
        loader = SkillLoader(skills_dir)
        assert loader.load_all() == []

    def test_load_skill_with_frontmatter(self, skills_dir):
        (skills_dir / "review.md").write_text(
            "---\n"
            "name: code-review\n"
            "description: Review code quality\n"
            "tools: [read, grep, glob]\n"
            "---\n"
            "You are a code reviewer."
        )
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        assert len(skills) == 1
        s = skills[0]
        assert s.name == "code-review"
        assert s.description == "Review code quality"
        assert s.tools == ["read", "grep", "glob"]
        assert "code reviewer" in s.prompt

    def test_load_skill_without_frontmatter(self, skills_dir):
        (skills_dir / "simple.md").write_text("Just a prompt, no frontmatter.")
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        assert len(skills) == 1
        assert skills[0].name == "simple"
        assert skills[0].prompt == "Just a prompt, no frontmatter."

    def test_load_by_name(self, skills_dir):
        (skills_dir / "test-runner.md").write_text(
            "---\nname: test-runner\ndescription: Run tests\n---\nRun pytest."
        )
        loader = SkillLoader(skills_dir)
        s = loader.load_by_name("test-runner")
        assert s is not None
        assert s.name == "test-runner"

    def test_load_by_name_nonexistent(self, skills_dir):
        loader = SkillLoader(skills_dir)
        assert loader.load_by_name("nope") is None

    def test_multiple_skills_sorted(self, skills_dir):
        (skills_dir / "b-skill.md").write_text("---\nname: bravo\n---\nB")
        (skills_dir / "a-skill.md").write_text("---\nname: alpha\n---\nA")
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        assert [s.name for s in skills] == ["alpha", "bravo"]

    def test_permissions_parsed(self, skills_dir):
        (skills_dir / "secure.md").write_text(
            "---\nname: secure\npermissions:\n  mode: plan\n---\nSafe."
        )
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        assert skills[0].permissions == {"mode": "plan"}

    def test_non_md_files_ignored(self, skills_dir):
        (skills_dir / "config.json").write_text('{"key": "value"}')
        (skills_dir / "real.md").write_text("---\nname: real\n---\nYes")
        loader = SkillLoader(skills_dir)
        assert len(loader.load_all()) == 1

    def test_broken_yaml_graceful(self, skills_dir):
        (skills_dir / "broken.md").write_text("---\n: invalid yaml {{{\n---\nContent")
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        # Should not crash, falls back to simple parser or empty meta
        assert len(skills) == 1


class TestSimpleYamlParser:
    def test_parse_key_value(self):
        result = SkillLoader._parse_simple_yaml("name: test\ndescription: a thing")
        assert result["name"] == "test"
        assert result["description"] == "a thing"

    def test_parse_list(self):
        result = SkillLoader._parse_simple_yaml("tools: [read, write, grep]")
        assert result["tools"] == ["read", "write", "grep"]

    def test_parse_empty(self):
        result = SkillLoader._parse_simple_yaml("")
        assert result == {}
