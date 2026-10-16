"""Tests for engine.voice_commands — voice shortcut parser."""

import pytest
from engine.voice_commands import VoiceCommandParser


@pytest.fixture
def parser():
    return VoiceCommandParser()


class TestVoiceCommands:
    def test_switch_profile_cn(self, parser):
        cmd, params = parser.parse("用 coding 模式")
        assert cmd is not None
        assert cmd.id == "switch-profile"
        assert params["match"] == "coding"

    def test_switch_profile_cn_2(self, parser):
        cmd, _ = parser.parse("切换到 review 模式")
        assert cmd is not None
        assert cmd.id == "switch-profile"

    def test_switch_profile_en(self, parser):
        cmd, params = parser.parse("switch to security mode")
        assert cmd is not None
        assert params["match"] == "security"

    def test_show_status_cn(self, parser):
        cmd, _ = parser.parse("看看引擎状态")
        assert cmd is not None
        assert cmd.id == "show-status"

    def test_show_status_en(self, parser):
        cmd, _ = parser.parse("show engine status")
        assert cmd is not None
        assert cmd.id == "show-status"

    def test_export_obsidian(self, parser):
        cmd, _ = parser.parse("导出到 Obsidian")
        assert cmd is not None
        assert cmd.id == "export-obsidian"

    def test_show_costs_cn(self, parser):
        cmd, _ = parser.parse("今天花了多少钱")
        assert cmd is not None
        assert cmd.id == "show-costs"

    def test_show_costs_en(self, parser):
        cmd, _ = parser.parse("show me the cost")
        assert cmd is not None
        assert cmd.id == "show-costs"

    def test_cancel_all(self, parser):
        cmd, _ = parser.parse("全部取消")
        assert cmd is not None
        assert cmd.id == "cancel-all"

    def test_show_tasks(self, parser):
        cmd, _ = parser.parse("看看任务列表")
        assert cmd is not None
        assert cmd.id == "show-tasks"

    def test_show_agents(self, parser):
        cmd, _ = parser.parse("show agents")
        assert cmd is not None
        assert cmd.id == "show-agents"

    def test_run_template(self, parser):
        cmd, params = parser.parse("用 code review 模板")
        assert cmd is not None
        assert cmd.id == "run-template"

    def test_no_match(self, parser):
        cmd, _ = parser.parse("今天天气怎么样")
        assert cmd is None

    def test_partial_no_match(self, parser):
        cmd, _ = parser.parse("这段代码需要修改一下")
        assert cmd is None

    def test_list_commands(self, parser):
        cmds = parser.list_commands()
        assert len(cmds) >= 8
        ids = {c.id for c in cmds}
        assert "switch-profile" in ids
        assert "show-status" in ids
