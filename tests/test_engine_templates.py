"""Tests for engine.templates — reusable task definitions."""

import json
import pytest
from engine.templates import TemplateManager, TaskTemplate, BUILTIN_TEMPLATES


@pytest.fixture
def mgr(tmp_path):
    return TemplateManager(tmp_path / "templates")


class TestTemplateManager:
    def test_builtins_loaded(self, mgr):
        templates = mgr.list_all()
        assert len(templates) >= len(BUILTIN_TEMPLATES)

    def test_get_builtin(self, mgr):
        t = mgr.get("code-review")
        assert t is not None
        assert t.name == "代码审查"

    def test_get_nonexistent(self, mgr):
        assert mgr.get("nope") is None

    def test_render(self, mgr):
        result = mgr.render("code-review", "auth.py module")
        assert result is not None
        assert "auth.py module" in result["prompt"]
        assert result["agent_id"] == "claude-code"
        assert result["priority"] == 1

    def test_render_nonexistent(self, mgr):
        assert mgr.render("nope", "input") is None

    def test_render_title(self, mgr):
        result = mgr.render("fix-bug", "login page crashes on empty email")
        assert "修复 Bug" in result["title"]
        assert "login page" in result["title"]


class TestUserTemplates:
    def test_save_and_load(self, tmp_path):
        mgr = TemplateManager(tmp_path / "templates")
        custom = TaskTemplate(
            id="deploy-staging",
            name="部署到测试环境",
            description="Deploy to staging",
            prompt_template="Deploy {input} to staging environment",
            tags=["deploy", "staging"],
        )
        mgr.save_user_template(custom)

        # Reload
        mgr2 = TemplateManager(tmp_path / "templates")
        t = mgr2.get("deploy-staging")
        assert t is not None
        assert t.name == "部署到测试环境"

    def test_delete_user_template(self, mgr):
        custom = TaskTemplate(id="temp", name="Temp")
        mgr.save_user_template(custom)
        assert mgr.get("temp") is not None

        assert mgr.delete_template("temp")
        assert mgr.get("temp") is None

    def test_cannot_delete_builtin(self, mgr):
        assert not mgr.delete_template("code-review")
        assert mgr.get("code-review") is not None

    def test_user_overrides_builtin(self, tmp_path):
        # Pre-create a user template with same id as builtin
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "code-review.json").write_text(json.dumps({
            "id": "code-review",
            "name": "Custom Review",
            "prompt_template": "Custom: {input}",
        }))
        mgr = TemplateManager(templates_dir)
        t = mgr.get("code-review")
        assert t.name == "Custom Review"


class TestKeywordMatch:
    def test_match_by_tag(self, mgr):
        t = mgr.match_by_keywords("please review this code")
        assert t is not None
        assert t.id == "code-review"

    def test_match_by_name(self, mgr):
        t = mgr.match_by_keywords("帮我运行测试")
        assert t is not None
        assert t.id == "run-tests"

    def test_match_fix(self, mgr):
        t = mgr.match_by_keywords("fix this login bug please")
        assert t is not None
        assert "fix" in t.id or "bug" in t.tags

    def test_match_docs(self, mgr):
        t = mgr.match_by_keywords("write docs for the API")
        assert t is not None
        assert t.id == "write-docs"

    def test_no_match(self, mgr):
        t = mgr.match_by_keywords("今天天气怎么样")
        assert t is None

    def test_match_search(self, mgr):
        t = mgr.match_by_keywords("search for the auth function")
        assert t is not None
        assert t.id == "search-code"
