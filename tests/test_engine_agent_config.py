"""Tests for engine.agent_config — scan ~/.osborn/config/agents/."""

import pytest
from engine.agent_config import AgentConfigScanner


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    return d


@pytest.fixture
def scanner(agents_dir):
    return AgentConfigScanner(agents_dir)


def make_agent(agents_dir, name, frontmatter):
    d = agents_dir / name
    d.mkdir()
    (d / "identity.md").write_text(f"---\n{frontmatter}\n---\nDescription of {name}")


class TestAgentScan:
    def test_empty_dir(self, scanner):
        assert scanner.scan() == []

    def test_single_agent(self, scanner, agents_dir):
        make_agent(agents_dir, "claude-code",
                   "id: claude-code\nname: Claude Code\nicon: 👨‍💻\nstatus: active")
        agents = scanner.scan()
        assert len(agents) == 1
        assert agents[0].id == "claude-code"
        assert agents[0].name == "Claude Code"
        assert agents[0].emoji == "👨‍💻"

    def test_multiple_agents(self, scanner, agents_dir):
        make_agent(agents_dir, "claude-code", "id: claude-code\nname: Claude Code\nstatus: active")
        make_agent(agents_dir, "openclaw", "id: openclaw\nname: OpenClaw\nicon: 🦞\nstatus: active")
        make_agent(agents_dir, "osborn", "id: osborn\nname: OSBORN\nicon: 🧠\nstatus: active")
        agents = scanner.scan()
        assert len(agents) == 3
        ids = {a.id for a in agents}
        assert ids == {"claude-code", "openclaw", "osborn"}

    def test_inactive_agent_skipped(self, scanner, agents_dir):
        make_agent(agents_dir, "active", "id: active\nname: Active\nstatus: active")
        make_agent(agents_dir, "inactive", "id: inactive\nname: Inactive\nstatus: inactive")
        agents = scanner.scan()
        assert len(agents) == 1
        assert agents[0].id == "active"

    def test_archive_dir_skipped(self, scanner, agents_dir):
        make_agent(agents_dir, "good", "id: good\nname: Good\nstatus: active")
        archive = agents_dir / "archive"
        archive.mkdir()
        (archive / "identity.md").write_text("---\nid: old\n---\nOld agent")
        agents = scanner.scan()
        assert len(agents) == 1

    def test_parent_id(self, scanner, agents_dir):
        make_agent(agents_dir, "sub-agent", "id: sub-agent\nname: Sub\nparent: osborn\nstatus: active")
        agents = scanner.scan()
        assert agents[0].parent_id == "osborn"

    def test_provider(self, scanner, agents_dir):
        make_agent(agents_dir, "codex", "id: codex\nname: Codex\nprovider: codex\nstatus: active")
        agents = scanner.scan()
        assert agents[0].provider == "codex"

    def test_default_provider(self, scanner, agents_dir):
        make_agent(agents_dir, "test", "id: test\nname: Test\nstatus: active")
        agents = scanner.scan()
        assert agents[0].provider == "claude"  # default

    def test_external_system(self, scanner, agents_dir):
        make_agent(agents_dir, "oc", "id: oc\nname: OC\nexternal_system: openclaw\nstatus: active")
        agents = scanner.scan()
        assert agents[0].external_system == "openclaw"

    def test_get_agent_by_id(self, scanner, agents_dir):
        make_agent(agents_dir, "claude", "id: claude\nname: Claude\nstatus: active")
        config = scanner.get_agent("claude")
        assert config is not None
        assert config.name == "Claude"

    def test_get_nonexistent(self, scanner, agents_dir):
        assert scanner.get_agent("nope") is None

    def test_no_dir(self, tmp_path):
        scanner = AgentConfigScanner(tmp_path / "nonexistent")
        assert scanner.scan() == []

    def test_missing_identity_file(self, scanner, agents_dir):
        (agents_dir / "empty-agent").mkdir()
        agents = scanner.scan()
        assert len(agents) == 0

    def test_hidden_dirs_skipped(self, scanner, agents_dir):
        make_agent(agents_dir, "good", "id: good\nname: Good\nstatus: active")
        hidden = agents_dir / ".hidden"
        hidden.mkdir()
        (hidden / "identity.md").write_text("---\nid: hidden\n---\n")
        agents = scanner.scan()
        assert len(agents) == 1

    def test_markdown_kv_fallback(self, scanner, agents_dir):
        d = agents_dir / "md-agent"
        d.mkdir()
        (d / "identity.md").write_text(
            "# MD Agent\n\n- **Name:** Markdown Agent\n- **Status:** active\n- **Icon:** 📄\n"
        )
        agents = scanner.scan()
        assert len(agents) == 1
        assert agents[0].name == "Markdown Agent"

    def test_skills_list(self, scanner, agents_dir):
        make_agent(agents_dir, "skilled",
                   "id: skilled\nname: Skilled\nstatus: active\nskills: [code-review, test-runner]")
        agents = scanner.scan()
        assert agents[0].skills == ["code-review", "test-runner"]
