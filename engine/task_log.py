"""Task Log — structured execution log for debugging and replay.

Records every step of task execution as a timeline:
- Intent classification
- Agent selection
- Tool calls with inputs/outputs
- LLM calls with token counts
- Status transitions
- Errors and retries

Enables post-hoc debugging: "why did task X fail?"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    timestamp: str
    task_id: str
    step: str  # "intent" | "dispatch" | "tool_call" | "tool_result" | "llm_call" | "status" | "error"
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


class TaskLog:
    """Structured execution log per task.

    Each task gets a log file under the Vox Agent data directory.
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or (resolve_vox_agent_data_dir() / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, task_id: str, step: str, **data: Any) -> LogEntry:
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            task_id=task_id,
            step=step,
            data=data,
        )
        self._append(entry)
        return entry

    def log_intent(self, task_id: str, text: str, intent: str, confidence: float) -> LogEntry:
        return self.log(task_id, "intent", text=text[:200], intent=intent, confidence=confidence)

    def log_dispatch(self, task_id: str, agent_id: str, provider: str) -> LogEntry:
        return self.log(task_id, "dispatch", agent_id=agent_id, provider=provider)

    def log_tool_call(self, task_id: str, tool: str, input_data: dict) -> LogEntry:
        return self.log(task_id, "tool_call", tool=tool, input=_truncate_dict(input_data))

    def log_tool_result(self, task_id: str, tool: str, output: str, success: bool) -> LogEntry:
        return self.log(task_id, "tool_result", tool=tool, output=output[:500], success=success)

    def log_llm_call(self, task_id: str, model: str, tokens_in: int, tokens_out: int, cost: float) -> LogEntry:
        return self.log(task_id, "llm_call", model=model, tokens_in=tokens_in, tokens_out=tokens_out, cost=cost)

    def log_status(self, task_id: str, from_status: str, to_status: str) -> LogEntry:
        return self.log(task_id, "status", from_status=from_status, to_status=to_status)

    def log_error(self, task_id: str, error: str, recoverable: bool = False) -> LogEntry:
        return self.log(task_id, "error", error=error[:500], recoverable=recoverable)

    def get_log(self, task_id: str) -> list[LogEntry]:
        """Read all log entries for a task."""
        path = self._path(task_id)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    entries.append(LogEntry(**d))
                except (json.JSONDecodeError, TypeError):
                    continue
        return entries

    def get_errors(self, task_id: str) -> list[LogEntry]:
        return [e for e in self.get_log(task_id) if e.step == "error"]

    def get_timeline(self, task_id: str) -> list[dict]:
        """Get a human-readable timeline."""
        entries = self.get_log(task_id)
        timeline = []
        for e in entries:
            line = {"time": e.timestamp[-12:-1], "step": e.step}
            if e.step == "intent":
                line["detail"] = f"{e.data.get('intent')} ({e.data.get('confidence', 0):.0%})"
            elif e.step == "dispatch":
                line["detail"] = f"→ {e.data.get('agent_id')} ({e.data.get('provider')})"
            elif e.step == "tool_call":
                line["detail"] = f"{e.data.get('tool')}()"
            elif e.step == "tool_result":
                status = "✓" if e.data.get("success") else "✗"
                line["detail"] = f"{status} {e.data.get('tool')}"
            elif e.step == "status":
                line["detail"] = f"{e.data.get('from_status')} → {e.data.get('to_status')}"
            elif e.step == "error":
                line["detail"] = e.data.get("error", "")[:80]
            elif e.step == "llm_call":
                line["detail"] = f"{e.data.get('model')} ${e.data.get('cost', 0):.4f}"
            else:
                line["detail"] = json.dumps(e.data)[:80]
            timeline.append(line)
        return timeline

    def cleanup(self, max_age_days: int = 30) -> int:
        """Remove log files older than max_age_days."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
        removed = 0
        for path in self.log_dir.glob("*.jsonl"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

    def _append(self, entry: LogEntry) -> None:
        path = self._path(entry.task_id)
        with open(path, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def _path(self, task_id: str) -> Path:
        safe_id = task_id.replace("/", "-").replace("\\", "-")
        return self.log_dir / f"{safe_id}.jsonl"


def _truncate_dict(d: dict, max_value_len: int = 200) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > max_value_len:
            result[k] = v[:max_value_len] + "..."
        else:
            result[k] = v
    return result
