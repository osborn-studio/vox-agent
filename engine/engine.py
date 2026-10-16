"""Agent Execution Engine — CLI subprocess + API direct dual-mode (OpenHarness style)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Any

from .models import EngineEvent, EventType
from .permissions import PermissionManager, ToolCall
from .hooks import HookChain
from .skills import Skill, SkillLoader
from .tools import ToolRegistry
from .agent_memory import AgentMemory
from .cli_environment import cli_environment

logger = logging.getLogger(__name__)


class AgentEngine:
    """Execute agent tasks in two modes:

    1. CLI subprocess — spawn claude/codex CLI, parse structured stdout
    2. API direct — call LLM API with tool registry (OpenHarness loop)
    """

    def __init__(
        self,
        tools: ToolRegistry,
        permissions: PermissionManager,
        hooks: HookChain,
        skills: SkillLoader,
        memory: AgentMemory | None = None,
    ) -> None:
        self.tools = tools
        self.permissions = permissions
        self.hooks = hooks
        self.skills = skills
        self.memory = memory or AgentMemory()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        agent_id: str,
        prompt: str,
        *,
        task_id: str = "",
        provider: str = "claude",
        skill_names: list[str] | None = None,
        requester_user_id: str = "",
        workspace_id: str = "",
        working_directory: str | None = None,
    ) -> AsyncGenerator[EngineEvent, None]:
        """Execute a task, yielding streaming events."""
        if provider in ("claude", "codex", "opencode"):
            async for event in self._run_cli(
                agent_id, prompt, provider, task_id=task_id, working_directory=working_directory
            ):
                yield event
        else:
            loaded_skills = []
            for name in (skill_names or []):
                s = self.skills.load_by_name(name)
                if s:
                    loaded_skills.append(s)
            async for event in self._run_direct(
                agent_id,
                prompt,
                provider,
                loaded_skills,
                task_id=task_id,
                requester_user_id=requester_user_id,
                workspace_id=workspace_id,
            ):
                yield event

    async def stop(self, task_id: str) -> None:
        proc = self._processes.pop(task_id, None)
        if proc and proc.returncode is None:
            proc.terminate()
            logger.info("terminated process for task %s", task_id)

    # -- Mode 1: CLI subprocess (Multica-style) ------------------------------

    async def _run_cli(
        self, agent_id: str, prompt: str, provider: str, *, task_id: str = "",
        working_directory: str | None = None
    ) -> AsyncGenerator[EngineEvent, None]:
        cmd = self._build_cli_command(provider, prompt)
        logger.info("spawning CLI: %s", cmd[0])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=cli_environment(),
            cwd=working_directory,
        )
        process_key = task_id or agent_id
        self._processes[process_key] = proc

        try:
            assert proc.stdout
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                event = self._parse_cli_line(line, provider)
                if event:
                    yield event

            returncode = await proc.wait()
            if returncode == 0:
                yield EngineEvent(type=EventType.DONE, result={"exit_code": 0})
            else:
                stderr = ""
                if proc.stderr:
                    stderr = (await proc.stderr.read()).decode(errors="replace")
                yield EngineEvent(type=EventType.ERROR, data=stderr)
        finally:
            self._processes.pop(process_key, None)

    def _build_cli_command(self, provider: str, prompt: str) -> list[str]:
        if provider == "claude":
            return ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        elif provider == "codex":
            return ["codex", "exec", prompt]
        else:
            return [provider, prompt]

    def _parse_cli_line(self, line: str, provider: str) -> EngineEvent | None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return EngineEvent(type=EventType.TEXT_DELTA, data=line)

        event_type = data.get("type", "")

        # Claude CLI: type="assistant" contains the actual response text
        if event_type == "assistant":
            msg = data.get("message", {})
            content_blocks = msg.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            if text_parts:
                return EngineEvent(type=EventType.TEXT_DELTA, data="".join(text_parts))
            return None

        # Claude CLI: type="content_block_delta" for streaming chunks
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return EngineEvent(type=EventType.TEXT_DELTA, data=delta.get("text", ""))

        # Generic text
        if event_type == "text" or (isinstance(data.get("content"), str) and event_type not in ("system",)):
            return EngineEvent(
                type=EventType.TEXT_DELTA,
                data=data.get("content", data.get("text", "")),
            )

        # Tool use
        if event_type == "tool_use":
            return EngineEvent(
                type=EventType.TOOL_USE,
                tool=data.get("tool", data.get("name", "")),
                input=data.get("input", {}),
            )
        if event_type == "tool_result":
            return EngineEvent(
                type=EventType.TOOL_DONE,
                tool=data.get("tool", data.get("name", "")),
                output=data.get("output", ""),
            )

        # Final result
        if event_type == "result":
            return EngineEvent(type=EventType.DONE, result=data)

        return None

    # -- Mode 2: API direct (OpenHarness-style loop) -------------------------

    async def _run_direct(
        self,
        agent_id: str,
        prompt: str,
        provider: str,
        skills: list[Skill],
        *,
        task_id: str = "",
        requester_user_id: str = "",
        workspace_id: str = "",
    ) -> AsyncGenerator[EngineEvent, None]:

        messages = [{"role": "user", "content": prompt}]
        system_prompt = self._build_system_prompt(skills, agent_id=agent_id)
        tool_defs = self._build_tool_definitions()

        max_iterations = 20
        for _ in range(max_iterations):
            # call LLM
            response = await self._call_llm(provider, system_prompt, messages, tool_defs)

            # stream text
            if response.get("text"):
                yield EngineEvent(type=EventType.TEXT_DELTA, data=response["text"])

            # check for tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                yield EngineEvent(
                    type=EventType.DONE,
                    result={"output": response.get("text", "")},
                )
                return

            # execute each tool call
            for tc in tool_calls:
                tool_call = ToolCall(tool=tc["name"], input=tc.get("input", {}))

                # permission check
                if not await self.permissions.check(
                    tool_call,
                    task_id=task_id,
                    agent_id=agent_id,
                    requester_user_id=requester_user_id,
                    workspace_id=workspace_id,
                ):
                    yield EngineEvent(
                        type=EventType.ERROR,
                        data=f"permission denied: {tc['name']}",
                    )
                    continue

                # pre-hook
                tool_call = await self.hooks.run_pre(agent_id, tool_call)
                if tool_call.blocked:
                    yield EngineEvent(
                        type=EventType.ERROR,
                        data=f"blocked by hook: {tool_call.block_reason}",
                    )
                    continue

                yield EngineEvent(
                    type=EventType.TOOL_USE,
                    tool=tc["name"],
                    input=tc.get("input", {}),
                )

                # execute
                result = await self.tools.execute(tc["name"], tc.get("input", {}))

                # post-hook
                result = await self.hooks.run_post(agent_id, tool_call, result)

                yield EngineEvent(
                    type=EventType.TOOL_DONE,
                    tool=tc["name"],
                    output=result.output if hasattr(result, "output") else str(result),
                )

                # inject result back into messages
                messages.append({
                    "role": "assistant",
                    "content": response.get("text", ""),
                    "tool_calls": [tc],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result.output if hasattr(result, "output") else str(result),
                })

        yield EngineEvent(type=EventType.ERROR, data="max iterations reached")

    def _build_system_prompt(self, skills: list[Skill], agent_id: str = "") -> str:
        parts = ["You are a Vox Agents task executor."]
        # inject agent memory
        if agent_id:
            memory_ctx = self.memory.get_context(agent_id)
            if memory_ctx:
                parts.append(f"\n{memory_ctx}")
        for s in skills:
            parts.append(f"\n## Skill: {s.name}\n{s.prompt}")
        return "\n".join(parts)

    def _build_tool_definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools.list_tools()
        ]

    async def _call_llm(
        self,
        provider: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """Call LLM API. Returns {"text": str, "tool_calls": list}."""
        import httpx

        # default to OpenAI-compatible API
        api_key = ""
        base_url = "https://api.openai.com/v1"
        model = "gpt-4o"

        if provider == "deepseek":
            base_url = "https://api.deepseek.com/v1"
            model = "deepseek-chat"
        elif provider == "qwen":
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            model = "qwen-turbo"

        async with httpx.AsyncClient(timeout=120) as client:
            body: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            if tools:
                body["tools"] = tools

            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        return {
            "text": msg.get("content", ""),
            "tool_calls": [
                {
                    "id": tc.get("id", ""),
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"].get("arguments", "{}")),
                }
                for tc in msg.get("tool_calls", [])
            ],
        }
