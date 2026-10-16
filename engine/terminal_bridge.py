"""Terminal Bridge — manage persistent Claude Code CLI sessions.

Each cc session is a Terminal.app window that stays open.
Vox Agents sends subsequent messages to the SAME window.

Sessions are named: CC1, CC2, CC3...
User creates new sessions from the sidebar.

Flow:
1. Create session CC1 → Terminal opens → cc starts → interface appears
2. Send "帮我看 git log" → types into CC1's terminal (same window)
3. Send "再看看 branch" → types into CC1's terminal (same window)
4. Create CC2 → new Terminal window → new cc instance
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CCSession:
    id: str           # "CC1", "CC2", etc.
    tab_id: str       # AppleScript tab reference
    working_dir: str
    created: bool = True
    message_count: int = 0


class TerminalBridge:
    """Manage persistent cc sessions in Terminal.app windows."""

    def __init__(self, default_dir: str | None = None) -> None:
        self.default_dir = default_dir or str(Path.home())
        self._sessions: dict[str, CCSession] = {}
        self._counter = 0

    # -- Session lifecycle ---------------------------------------------------

    async def create_session(self, working_dir: str | None = None, session_id: str | None = None) -> dict:
        """Create a new cc session (opens Terminal, runs cc).

        Returns session info. cc takes ~5 seconds to load.
        """
        cwd = working_dir or self.default_dir

        if not session_id:
            self._counter += 1
            session_id = f"CC{self._counter}"

        # Open Terminal and run cc
        apple_script = f'''
        tell application "Terminal"
            activate
            set newTab to do script "source ~/.zshrc 2>/dev/null; cd \\"{cwd}\\" && echo '[Vox Agents] Session {session_id} starting...' && cc"
            return id of window 1
        end tell
        '''

        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", apple_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            tab_id = stdout.decode().strip()

            session = CCSession(
                id=session_id,
                tab_id=tab_id,
                working_dir=cwd,
            )
            self._sessions[session_id] = session

            logger.info("cc session created: %s in %s (window %s)", session_id, cwd, tab_id)
            return {
                "status": "created",
                "session_id": session_id,
                "working_dir": cwd,
                "note": f"Claude Code CLI session {session_id} starting. Wait ~5s for cc to load.",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def send_to_session(self, session_id: str, prompt: str) -> dict:
        """Send a prompt to an existing cc session.

        Types into the Terminal window where cc is running.
        Does NOT open a new Terminal.
        """
        session = self._sessions.get(session_id)

        # If session doesn't exist, create one first
        if not session:
            result = await self.create_session(session_id=session_id)
            if result["status"] != "created":
                return result
            session = self._sessions[session_id]
            # Wait for cc to load
            await asyncio.sleep(6)

        escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')

        # Type into the EXISTING Terminal window (don't create new one)
        type_script = f'''
        tell application "Terminal"
            activate
            -- Find the window for this session
            set targetWindow to window id {session.tab_id}
            set frontmost of targetWindow to true
        end tell
        delay 0.5
        tell application "System Events"
            tell process "Terminal"
                keystroke "{escaped_prompt}"
                delay 0.3
                keystroke return
            end tell
        end tell
        '''

        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", type_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                session.message_count += 1
                logger.info("[%s] prompt #%d sent: %s", session_id, session.message_count, prompt[:50])
                return {
                    "status": "sent",
                    "session_id": session_id,
                    "message_number": session.message_count,
                    "prompt": prompt[:100],
                }
            else:
                # Window might have been closed — try creating new session
                error = stderr.decode(errors="replace")
                if "window id" in error.lower() or "can't get" in error.lower():
                    logger.warning("[%s] window lost, recreating...", session_id)
                    del self._sessions[session_id]
                    return await self.send_to_session(session_id, prompt)
                return {"status": "error", "error": error}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def close_session(self, session_id: str) -> bool:
        """Mark a session as closed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self) -> list[dict]:
        """List all active cc sessions."""
        return [
            {
                "id": s.id,
                "working_dir": s.working_dir,
                "messages": s.message_count,
            }
            for s in self._sessions.values()
        ]

    def get_session(self, session_id: str) -> CCSession | None:
        return self._sessions.get(session_id)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    # -- Aliases for orchestrator compatibility ------------------------------

    async def launch_cc(self, prompt: str, working_dir: str | None = None,
                        new_window: bool = True) -> dict:
        """Default: send to CC1, creating if needed."""
        return await self.send_to_session("CC1", prompt)

    async def launch_interactive(self, working_dir: str | None = None) -> dict:
        """Start a new interactive session."""
        return await self.create_session(working_dir)
