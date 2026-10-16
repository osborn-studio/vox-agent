import asyncio

import pytest

from engine.engine import AgentEngine


@pytest.mark.asyncio
async def test_cli_process_is_indexed_by_task_id_for_cancellation(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    class FakeStdout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            started.set()
            await release.wait()
            raise StopAsyncIteration

    class FakeStderr:
        async def read(self):
            return b"terminated"

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.returncode = None
            self.terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            return self.returncode or 0

    process = FakeProcess()

    async def fake_subprocess(*args, **kwargs):
        assert kwargs["cwd"] == "/tmp/repo with spaces"
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    engine = AgentEngine(tools=None, permissions=None, hooks=None, skills=None)

    async def consume():
        return [
            event
            async for event in engine.run(
                "agent-1", "work", task_id="task-123", provider="codex",
                working_directory="/tmp/repo with spaces"
            )
        ]

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert "task-123" in engine._processes
    assert "agent-1" not in engine._processes

    await engine.stop("task-123")
    release.set()
    await consumer
    assert process.terminated
