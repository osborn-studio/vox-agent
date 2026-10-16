"""Tests for engine.profiles — agent behavior presets."""

import pytest
from engine.profiles import ProfileManager, AgentProfile, BUILTIN_PROFILES


@pytest.fixture
def mgr(tmp_path):
    return ProfileManager(tmp_path / "profiles")


class TestProfileManager:
    def test_builtins_loaded(self, mgr):
        profiles = mgr.list_all()
        assert len(profiles) >= len(BUILTIN_PROFILES)

    def test_get_coding(self, mgr):
        p = mgr.get("coding")
        assert p is not None
        assert p.permission_mode == "auto"
        assert "code-review" in p.skills

    def test_get_review(self, mgr):
        p = mgr.get("review")
        assert p is not None
        assert p.permission_mode == "plan"

    def test_get_security(self, mgr):
        p = mgr.get("security")
        assert p is not None
        assert "security-audit" in p.skills

    def test_get_nonexistent(self, mgr):
        assert mgr.get("nope") is None

    def test_apply(self, mgr):
        config = mgr.apply("coding")
        assert config is not None
        assert config["permission_mode"] == "auto"
        assert config["max_iterations"] == 30

    def test_apply_nonexistent(self, mgr):
        assert mgr.apply("nope") is None


class TestCustomProfiles:
    def test_save_and_get(self, mgr):
        custom = AgentProfile(
            id="custom-deploy",
            name="Custom Deploy",
            permission_mode="default",
            skills=["deploy-staging"],
        )
        mgr.save(custom)
        assert mgr.get("custom-deploy") is not None

    def test_persistence(self, tmp_path):
        m1 = ProfileManager(tmp_path / "profiles")
        m1.save(AgentProfile(id="my-profile", name="Mine", skills=["test-runner"]))

        m2 = ProfileManager(tmp_path / "profiles")
        p = m2.get("my-profile")
        assert p is not None
        assert p.skills == ["test-runner"]

    def test_delete_custom(self, mgr):
        mgr.save(AgentProfile(id="temp", name="Temp"))
        assert mgr.delete("temp")
        assert mgr.get("temp") is None

    def test_cannot_delete_builtin(self, mgr):
        assert not mgr.delete("coding")
        assert mgr.get("coding") is not None

    def test_override_builtin(self, tmp_path):
        dir_ = tmp_path / "profiles"
        dir_.mkdir()
        import json
        (dir_ / "coding.json").write_text(json.dumps({
            "id": "coding", "name": "Custom Coding", "skills": ["custom-skill"],
        }))
        m = ProfileManager(dir_)
        p = m.get("coding")
        assert p.name == "Custom Coding"
