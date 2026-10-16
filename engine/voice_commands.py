"""Voice Commands — shortcut phrases for common actions.

Maps natural language patterns to specific engine operations:
- "用 review 模式" → switch to review profile
- "看看状态" → show engine health
- "导出到 Obsidian" → trigger Obsidian export
- "今天花了多少钱" → show cost summary
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VoiceCommand:
    id: str
    name: str
    patterns: list[str]  # regex patterns
    description: str
    handler_key: str  # maps to a handler function


BUILTIN_COMMANDS = [
    VoiceCommand(
        id="switch-profile",
        name="切换模式",
        patterns=[
            r"(用|切换到?|使用)\s*(coding|review|ops|research|security|编码|审查|运维|调研|安全)\s*模式",
            r"switch\s+to\s+(coding|review|ops|research|security)\s+mode",
        ],
        description="Switch agent profile/mode",
        handler_key="switch_profile",
    ),
    VoiceCommand(
        id="show-status",
        name="查看状态",
        patterns=[
            r"(看看|查看|显示)\s*(状态|引擎|engine)",
            r"(show|check)\s*(status|engine|health)",
            r"引擎状态",
        ],
        description="Show engine health status",
        handler_key="show_status",
    ),
    VoiceCommand(
        id="export-obsidian",
        name="导出 Obsidian",
        patterns=[
            r"导出.*obsidian",
            r"同步.*obsidian",
            r"export.*obsidian",
            r"sync.*obsidian",
        ],
        description="Export task history to Obsidian",
        handler_key="export_obsidian",
    ),
    VoiceCommand(
        id="show-costs",
        name="查看费用",
        patterns=[
            r"(花了多少|费用|cost|spend|花费)",
            r"(今天|today).*cost",
            r"API.*费用",
        ],
        description="Show API cost summary",
        handler_key="show_costs",
    ),
    VoiceCommand(
        id="cancel-all",
        name="全部取消",
        patterns=[
            r"(全部|所有).*取消",
            r"cancel\s+(all|everything)",
            r"停掉所有",
        ],
        description="Cancel all active tasks",
        handler_key="cancel_all",
    ),
    VoiceCommand(
        id="show-tasks",
        name="查看任务",
        patterns=[
            r"(看看|查看|显示|列出).*任务",
            r"(show|list)\s+tasks",
            r"任务列表",
        ],
        description="List active tasks",
        handler_key="show_tasks",
    ),
    VoiceCommand(
        id="show-agents",
        name="查看 Agent",
        patterns=[
            r"(看看|查看|显示|列出).*agent",
            r"(show|list)\s+agents",
            r"agent.*列表",
        ],
        description="List available agents",
        handler_key="show_agents",
    ),
    VoiceCommand(
        id="run-template",
        name="使用模板",
        patterns=[
            r"用\s*(code.?review|test.?runner|fix.?bug|write.?docs|refactor|search)\s*模板",
            r"use\s+(code.?review|test.?runner|fix.?bug|write.?docs|refactor|search)\s+template",
        ],
        description="Run a task from a template",
        handler_key="run_template",
    ),
]


class VoiceCommandParser:
    """Parse voice input for shortcut commands."""

    def __init__(self) -> None:
        self._commands: list[VoiceCommand] = list(BUILTIN_COMMANDS)
        self._compiled: list[tuple[re.Pattern, VoiceCommand]] = []
        self._compile()

    def _compile(self) -> None:
        self._compiled = []
        for cmd in self._commands:
            for pattern in cmd.patterns:
                self._compiled.append((
                    re.compile(pattern, re.IGNORECASE),
                    cmd,
                ))

    def register(self, command: VoiceCommand) -> None:
        self._commands.append(command)
        self._compile()

    def parse(self, text: str) -> tuple[VoiceCommand | None, dict[str, str]]:
        """Try to match text against voice commands.

        Returns (command, extracted_params) or (None, {}).
        """
        text = text.strip()
        for pattern, cmd in self._compiled:
            match = pattern.search(text)
            if match:
                params = {}
                groups = match.groups()
                if groups:
                    params["match"] = groups[-1]  # last capture group
                logger.debug("voice command: %s → %s", text[:50], cmd.id)
                return cmd, params
        return None, {}

    def list_commands(self) -> list[VoiceCommand]:
        return list(self._commands)
