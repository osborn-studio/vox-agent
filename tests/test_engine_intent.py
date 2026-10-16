"""Tests for engine.intent — voice input intent classification."""

import pytest
from engine.intent import IntentParser, Intent


@pytest.fixture
def parser():
    return IntentParser()


class TestCancel:
    def test_chinese_cancel(self, parser):
        assert parser.parse("停下来").intent == Intent.CANCEL

    def test_chinese_cancel_2(self, parser):
        assert parser.parse("算了").intent == Intent.CANCEL

    def test_english_cancel(self, parser):
        assert parser.parse("cancel").intent == Intent.CANCEL

    def test_english_stop(self, parser):
        assert parser.parse("stop").intent == Intent.CANCEL

    def test_cancel_high_confidence(self, parser):
        result = parser.parse("取消")
        assert result.confidence >= 0.9


class TestConfirm:
    def test_chinese_ok(self, parser):
        assert parser.parse("好的").intent == Intent.CONFIRM

    def test_chinese_pass(self, parser):
        assert parser.parse("通过").intent == Intent.CONFIRM

    def test_english_lgtm(self, parser):
        assert parser.parse("lgtm").intent == Intent.CONFIRM

    def test_english_ship(self, parser):
        assert parser.parse("ship it").intent == Intent.CONFIRM

    def test_long_text_not_confirm(self, parser):
        # "好的" in a longer sentence should not be confirm
        result = parser.parse("好的我觉得这个方案需要再讨论一下然后再决定用哪种方式来实现")
        assert result.intent != Intent.CONFIRM


class TestQuery:
    def test_chinese_status(self, parser):
        assert parser.parse("进度怎么样了").intent == Intent.QUERY

    def test_chinese_done(self, parser):
        assert parser.parse("做完了吗").intent == Intent.QUERY

    def test_english_status(self, parser):
        assert parser.parse("status?").intent == Intent.QUERY

    def test_english_progress(self, parser):
        assert parser.parse("how's it going?").intent == Intent.QUERY


class TestRecord:
    def test_chinese_note(self, parser):
        result = parser.parse("记一下：明天要开会讨论 auth")
        assert result.intent == Intent.RECORD
        assert "明天要开会讨论 auth" in result.extracted_title

    def test_english_note(self, parser):
        result = parser.parse("note: review PR 42 tomorrow")
        assert result.intent == Intent.RECORD


class TestTask:
    def test_chinese_help(self, parser):
        result = parser.parse("帮我检查一下测试覆盖率")
        assert result.intent == Intent.TASK
        assert "检查" in result.extracted_title

    def test_chinese_fix(self, parser):
        result = parser.parse("修一下 auth 模块的 bug")
        assert result.intent == Intent.TASK

    def test_english_write(self, parser):
        result = parser.parse("write unit tests for the engine module")
        assert result.intent == Intent.TASK
        assert "unit tests" in result.extracted_title

    def test_english_fix(self, parser):
        result = parser.parse("fix the login page CSS")
        assert result.intent == Intent.TASK

    def test_english_review(self, parser):
        result = parser.parse("review the latest PR changes")
        assert result.intent == Intent.TASK

    def test_title_extraction(self, parser):
        result = parser.parse("帮我写一个用户注册的接口")
        assert result.extracted_title
        assert len(result.extracted_title) <= 53  # 50 + "..."


class TestPriority:
    def test_urgent_chinese(self, parser):
        result = parser.parse("紧急修复生产环境的 bug")
        assert result.extracted_priority == "urgent"

    def test_urgent_english(self, parser):
        result = parser.parse("fix this ASAP, production is down")
        assert result.extracted_priority == "urgent"

    def test_high_chinese(self, parser):
        result = parser.parse("这个很重要，请尽快处理")
        assert result.extracted_priority == "high"

    def test_default_medium(self, parser):
        result = parser.parse("帮我加一个搜索功能")
        assert result.extracted_priority == "medium"


class TestChat:
    def test_with_agent_context(self, parser):
        result = parser.parse("你觉得用 PostgreSQL 还是 SQLite 好", agent_id="claude-code")
        assert result.intent == Intent.CHAT

    def test_without_agent_defaults_to_record(self, parser):
        result = parser.parse("今天天气不错啊")
        assert result.intent == Intent.RECORD


class TestEdgeCases:
    def test_empty_input(self, parser):
        result = parser.parse("")
        assert result.intent == Intent.RECORD
        assert result.confidence == 0.0

    def test_whitespace(self, parser):
        result = parser.parse("   ")
        assert result.intent == Intent.RECORD

    def test_mixed_signals(self, parser):
        # "帮我" (task) + short enough to be cancel territory
        result = parser.parse("帮我取消上次的部署")
        # task keyword takes priority because cancel requires short text
        assert result.intent in (Intent.TASK, Intent.CANCEL)

    def test_confidence_ordering(self, parser):
        # Cancel should have higher confidence than task
        cancel_conf = parser.parse("停下来").confidence
        task_conf = parser.parse("帮我写代码").confidence
        assert cancel_conf > task_conf
