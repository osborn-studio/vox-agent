"""Tests for engine.permissions — three-level access control."""

import asyncio

import pytest
from engine.permissions import (
    PermissionManager, PermissionConfig, PermissionMode,
    ToolCall, PathRule,
)


@pytest.fixture
def default_mgr():
    return PermissionManager(PermissionConfig(mode=PermissionMode.DEFAULT))


@pytest.fixture
def auto_mgr():
    return PermissionManager(PermissionConfig(
        mode=PermissionMode.AUTO,
        path_rules=[
            PathRule(pattern="/safe/*", allow=True),
            PathRule(pattern="/secret/*", allow=False),
        ],
    ))


@pytest.fixture
def plan_mgr():
    return PermissionManager(PermissionConfig(mode=PermissionMode.PLAN))


class TestPlanMode:
    @pytest.mark.asyncio
    async def test_read_allowed(self, plan_mgr):
        assert await plan_mgr.check(ToolCall(tool="read"))

    @pytest.mark.asyncio
    async def test_grep_allowed(self, plan_mgr):
        assert await plan_mgr.check(ToolCall(tool="grep"))

    @pytest.mark.asyncio
    async def test_glob_allowed(self, plan_mgr):
        assert await plan_mgr.check(ToolCall(tool="glob"))

    @pytest.mark.asyncio
    async def test_write_blocked(self, plan_mgr):
        assert not await plan_mgr.check(ToolCall(tool="write"))

    @pytest.mark.asyncio
    async def test_bash_blocked(self, plan_mgr):
        assert not await plan_mgr.check(ToolCall(tool="bash"))

    @pytest.mark.asyncio
    async def test_edit_blocked(self, plan_mgr):
        assert not await plan_mgr.check(ToolCall(tool="edit"))


class TestAutoMode:
    @pytest.mark.asyncio
    async def test_safe_path_allowed(self, auto_mgr):
        tc = ToolCall(tool="write", input={"file_path": "/safe/test.txt"})
        assert await auto_mgr.check(tc)

    @pytest.mark.asyncio
    async def test_secret_path_denied(self, auto_mgr):
        tc = ToolCall(tool="write", input={"file_path": "/secret/key.pem"})
        assert not await auto_mgr.check(tc)

    @pytest.mark.asyncio
    async def test_dangerous_command_denied(self, auto_mgr):
        tc = ToolCall(
            tool="bash",
            input={"command": "rm -rf " + "/"},
        )
        assert not await auto_mgr.check(tc)

    @pytest.mark.asyncio
    async def test_sudo_denied(self, auto_mgr):
        tc = ToolCall(tool="bash", input={"command": "sudo apt install"})
        assert not await auto_mgr.check(tc)

    @pytest.mark.asyncio
    async def test_safe_command_allowed(self, auto_mgr):
        tc = ToolCall(tool="bash", input={"command": "ls -la"})
        assert await auto_mgr.check(tc)

    @pytest.mark.asyncio
    async def test_no_path_allowed_by_default(self, auto_mgr):
        tc = ToolCall(tool="write", input={"content": "hello"})
        assert await auto_mgr.check(tc)


class TestDefaultMode:
    @pytest.mark.asyncio
    async def test_read_auto_pass(self, default_mgr):
        assert await default_mgr.check(ToolCall(tool="read"))

    @pytest.mark.asyncio
    async def test_grep_auto_pass(self, default_mgr):
        assert await default_mgr.check(ToolCall(tool="grep"))

    @pytest.mark.asyncio
    async def test_write_fails_closed_without_approval_notifier(self, default_mgr):
        tool_call = ToolCall(tool="write", input={"file_path": "/tmp/test"})

        assert await default_mgr.check(tool_call) is False


class TestApprovalResolution:
    @pytest.mark.asyncio
    async def test_pending_requests_are_available_for_reconciliation(self):
        request_published = asyncio.Event()

        async def notify(_request):
            request_published.set()

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
            approval_timeout_seconds=5,
        )
        pending_check = asyncio.create_task(
            manager.check(
                ToolCall(tool="write", input={"file_path": "/tmp/reconcile.txt"}),
                task_id="task-reconcile",
                agent_id="codex",
                requester_user_id="user-a",
                workspace_id="workspace-a",
            )
        )
        await request_published.wait()

        pending = manager.list_pending_requests()
        owned = manager.list_pending_requests_for_principal(
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )
        wrong_user = manager.list_pending_requests_for_principal(
            requester_user_id="user-b",
            workspace_id="workspace-a",
        )
        wrong_workspace = manager.list_pending_requests_for_principal(
            requester_user_id="user-a",
            workspace_id="workspace-b",
        )

        assert len(pending) == 1
        assert owned == pending
        assert wrong_user == []
        assert wrong_workspace == []
        assert pending[0].task_id == "task-reconcile"
        assert pending[0].agent_id == "codex"
        assert pending[0].tool == "write"
        assert not manager.resolve_approval_for_principal(
            pending[0].approval_id,
            False,
            requester_user_id="user-b",
            workspace_id="workspace-a",
        )
        assert manager.resolve_approval_for_principal(
            pending[0].approval_id,
            False,
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )
        assert not manager.resolve_approval_for_principal(
            pending[0].approval_id,
            False,
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )
        assert await pending_check is False
        assert manager.list_pending_requests() == []

    @pytest.mark.asyncio
    async def test_request_emits_safe_exact_scope_and_resolves(self):
        requests = []

        async def notify(request):
            requests.append(request)
            assert manager.resolve_approval(request.approval_id, True)

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
        )
        tool_call = ToolCall(
            tool="write",
            input={"file_path": "/tmp/vox-agent.txt", "content": "hello"},
        )

        allowed = await manager.check(
            tool_call,
            task_id="task-123",
            agent_id="codex",
        )

        assert allowed is True
        assert len(requests) == 1
        request = requests[0]
        assert request.task_id == "task-123"
        assert request.agent_id == "codex"
        assert request.tool == "write"
        assert request.scope == {
            "file_path": "/tmp/vox-agent.txt",
        }
        assert request.requested_at < request.expires_at

    @pytest.mark.asyncio
    async def test_remote_scope_excludes_private_content_and_redacts_secrets(self):
        requests = []

        async def notify(request):
            requests.append(request)
            assert manager.resolve_approval(request.approval_id, False)

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
        )

        allowed = await manager.check(ToolCall(
            tool="bash",
            input={
                "command": "OPENAI_API_KEY=private-value curl https://example.test",
                "working_directory": "/tmp/project",
                "content": "private transcript",
                "prompt": "private prompt",
                "token": "private-token",
                "secret": "private-secret",
            },
        ))

        assert allowed is False
        assert requests[0].scope == {
            "command": "[redacted: sensitive value]",
            "working_directory": "/tmp/project",
        }

    @pytest.mark.asyncio
    async def test_unowned_request_is_visible_only_when_explicitly_included(self):
        request_published = asyncio.Event()

        async def notify(_request):
            request_published.set()

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
            approval_timeout_seconds=5,
        )
        pending_check = asyncio.create_task(manager.check(
            ToolCall(tool="write", input={"file_path": "/tmp/local.txt"}),
            task_id="legacy-local-task",
            agent_id="codex",
        ))
        await request_published.wait()

        assert manager.list_pending_requests_for_principal(
            requester_user_id="local-admin",
            workspace_id="local-org",
        ) == []
        visible = manager.list_pending_requests_for_principal(
            requester_user_id="local-admin",
            workspace_id="local-org",
            include_unowned=True,
        )
        assert len(visible) == 1
        assert not manager.resolve_approval_for_principal(
            visible[0].approval_id,
            False,
            requester_user_id="local-admin",
            workspace_id="local-org",
        )
        assert manager.resolve_approval_for_principal(
            visible[0].approval_id,
            False,
            requester_user_id="local-admin",
            workspace_id="local-org",
            include_unowned=True,
        )
        assert await pending_check is False

    @pytest.mark.asyncio
    async def test_expired_request_disappears_and_cannot_be_resolved(self):
        request_published = asyncio.Event()
        published = []

        async def notify(request):
            published.append(request)
            request_published.set()

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
            approval_timeout_seconds=0.01,
        )
        pending_check = asyncio.create_task(manager.check(
            ToolCall(tool="write", input={"file_path": "/tmp/expired.txt"}),
            requester_user_id="user-a",
            workspace_id="workspace-a",
        ))
        await request_published.wait()
        assert len(manager.list_pending_requests_for_principal(
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )) == 1

        assert await pending_check is False
        assert manager.list_pending_requests_for_principal(
            requester_user_id="user-a",
            workspace_id="workspace-a",
        ) == []
        assert not manager.resolve_approval_for_principal(
            published[0].approval_id,
            True,
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )

    def test_unknown_or_expired_approval_cannot_be_resolved(self):
        manager = PermissionManager(PermissionConfig(mode=PermissionMode.DEFAULT))

        assert manager.resolve_approval("missing", True) is False

    @pytest.mark.asyncio
    async def test_always_for_file_persists_an_exact_tool_and_path_rule(self, tmp_path):
        requests = []
        rules_path = tmp_path / "permission_rules.json"

        async def notify(request):
            requests.append(request)
            assert manager.resolve_approval(
                request.approval_id,
                True,
                remember_scope=True,
            )

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
            rules_path=rules_path,
        )

        assert await manager.check(ToolCall(
            tool="write",
            input={"file_path": "/tmp/report.md", "content": "first"},
        ))
        assert rules_path.exists()

        restarted = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            rules_path=rules_path,
        )
        assert await restarted.check(ToolCall(
            tool="write",
            input={"file_path": "/tmp/report.md", "content": "second"},
        ))
        assert not await restarted.check(ToolCall(
            tool="write",
            input={"file_path": "/tmp/other.md", "content": "second"},
        ))
        assert not await restarted.check(ToolCall(
            tool="edit",
            input={"file_path": "/tmp/report.md", "content": "second"},
        ))
        assert len(requests) == 1

    @pytest.mark.asyncio
    async def test_remembered_file_rule_is_bound_to_requester_and_workspace(
        self,
        tmp_path,
    ):
        requests = []
        rules_path = tmp_path / "permission_rules.json"

        async def notify(request):
            requests.append(request)
            assert manager.resolve_approval_for_principal(
                request.approval_id,
                True,
                requester_user_id="user-a",
                workspace_id="workspace-a",
                remember_scope=True,
            )

        manager = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            on_request=notify,
            rules_path=rules_path,
        )
        tool_call = ToolCall(
            tool="write",
            input={"file_path": "/tmp/scoped-report.md", "content": "first"},
        )

        assert await manager.check(
            tool_call,
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )
        assert len(requests) == 1

        restarted = PermissionManager(
            PermissionConfig(mode=PermissionMode.DEFAULT),
            rules_path=rules_path,
        )
        assert await restarted.check(
            tool_call,
            requester_user_id="user-a",
            workspace_id="workspace-a",
        )
        assert not await restarted.check(
            tool_call,
            requester_user_id="user-b",
            workspace_id="workspace-a",
        )
        assert not await restarted.check(
            tool_call,
            requester_user_id="user-a",
            workspace_id="workspace-b",
        )

    @pytest.mark.asyncio
    async def test_resolve_approval(self):
        mgr = PermissionManager(PermissionConfig(mode=PermissionMode.DEFAULT))
        import asyncio

        tc = ToolCall(tool="write")
        approval_id = f"write:{id(tc)}"

        # simulate approval in background
        async def approve_later():
            await asyncio.sleep(0.05)
            mgr.resolve_approval(approval_id, True)

        future = asyncio.get_event_loop().create_future()
        mgr._pending_approvals[approval_id] = future
        asyncio.create_task(approve_later())

        result = await asyncio.wait_for(future, timeout=1.0)
        assert result is True
