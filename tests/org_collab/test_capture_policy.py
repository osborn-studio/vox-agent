"""Tests for configured workspace capture policy."""

from pathlib import Path

from engine.capture_policy import CapturePolicy


class TestCapturePolicy:
    def test_configured_local_workspace_defaults_to_capture_enabled(self, tmp_path):
        workspace = tmp_path / "vox-agent"
        workspace.mkdir()

        policy = CapturePolicy()
        configured = policy.register_workspace(
            org_id="org_1",
            local_path=workspace,
        )

        assert configured.capture_enabled is True
        assert configured.org_id == "org_1"
        assert configured.workspace_id
        assert configured.source_hash
        assert str(workspace) not in repr(configured)
        assert str(workspace) not in configured.__dict__.values()

    def test_unconfigured_workspace_or_repo_does_not_capture_by_default(self):
        policy = CapturePolicy()

        assert policy.is_capture_enabled("missing_workspace") is False
        assert policy.is_repo_capture_enabled(
            "https://github.com/example/private-repo.git"
        ) is False

    def test_configured_repo_url_defaults_to_capture_enabled_without_exposing_url(self):
        repo_url = "https://github.com/example/vox-agent.git"

        policy = CapturePolicy()
        configured = policy.register_workspace(
            org_id="org_1",
            repo_url=repo_url,
        )

        assert configured.capture_enabled is True
        assert policy.is_repo_capture_enabled(repo_url) is True
        assert repo_url not in repr(configured)
        assert repo_url not in configured.__dict__.values()

    def test_pause_and_resume_workspace_capture(self, tmp_path):
        workspace = tmp_path / "vox-agent"
        workspace.mkdir()

        policy = CapturePolicy()
        configured = policy.register_workspace("org_1", local_path=workspace)

        policy.pause_capture(configured.workspace_id)
        assert policy.is_capture_enabled(configured.workspace_id) is False
        assert policy.is_path_capture_allowed(workspace / "engine" / "server.py") is False

        policy.resume_capture(configured.workspace_id)
        assert policy.is_capture_enabled(configured.workspace_id) is True
        assert policy.is_path_capture_allowed(workspace / "engine" / "server.py") is True

    def test_file_path_must_be_under_configured_workspace(self, tmp_path):
        workspace = tmp_path / "vox-agent"
        sibling = tmp_path / "vox-agent-copy"
        workspace.mkdir()
        sibling.mkdir()
        inside_file = workspace / "engine" / "capture_policy.py"
        outside_file = sibling / "engine" / "capture_policy.py"

        policy = CapturePolicy()
        configured = policy.register_workspace("org_1", local_path=workspace)

        assert policy.workspace_for_path(inside_file) == configured.workspace_id
        assert policy.is_path_capture_allowed(inside_file) is True
        assert policy.workspace_for_path(outside_file) is None
        assert policy.is_path_capture_allowed(outside_file) is False

    def test_register_requires_exactly_one_source(self, tmp_path):
        policy = CapturePolicy()

        try:
            policy.register_workspace("org_1")
        except ValueError as exc:
            assert "local_path or repo_url" in str(exc)
        else:
            raise AssertionError("expected missing source to be rejected")

        try:
            policy.register_workspace(
                "org_1",
                local_path=Path(tmp_path),
                repo_url="https://github.com/example/repo.git",
            )
        except ValueError as exc:
            assert "exactly one" in str(exc)
        else:
            raise AssertionError("expected multiple sources to be rejected")
