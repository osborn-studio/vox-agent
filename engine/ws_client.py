#!/usr/bin/env python3
"""WebSocket test client — connect to the engine and display real-time events.

Usage:
    python -m engine.ws_client                    # default port 18900
    python -m engine.ws_client --port 18901       # custom port
    python -m engine.ws_client --task "run pytest" --agent claude-code  # create task and watch
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

import httpx


async def main():
    parser = argparse.ArgumentParser(description="Vox Agents WebSocket client")
    parser.add_argument("--port", type=int, default=18900)
    parser.add_argument("--task", help="Create a task and watch execution")
    parser.add_argument("--agent", default="claude-code")
    parser.add_argument("--provider", default="claude")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"

    # Check health
    try:
        r = httpx.get(f"{base}/health", timeout=3)
        print(f"Engine: {r.json()['status']} (port {args.port})")
    except httpx.ConnectError:
        print(f"Engine not running on port {args.port}")
        sys.exit(1)

    # Create task if requested
    task_id = None
    if args.task:
        r = httpx.post(f"{base}/v1/message", json={
            "text": args.task,
            "agent_id": args.agent,
            "provider": args.provider,
        }, timeout=10)
        result = r.json()
        task_id = result.get("task_id")
        print(f"Action: {result['action']}")
        if task_id:
            print(f"Task: {task_id}")
        print()

    # Connect WebSocket
    try:
        import websockets
    except ImportError:
        print("Install websockets: pip install websockets")
        print("Falling back to polling mode...")
        if task_id:
            await _poll_task(base, task_id)
        return

    ws_url = f"ws://127.0.0.1:{args.port}/ws"
    print(f"Connecting to {ws_url}...")

    async with websockets.connect(ws_url) as ws:
        print("Connected. Waiting for events...\n")
        try:
            async for msg in ws:
                event = json.loads(msg)
                _print_event(event)
        except KeyboardInterrupt:
            print("\nDisconnected.")


async def _poll_task(base: str, task_id: str):
    """Fallback: poll task status every second."""
    print(f"Polling task {task_id}...")
    last_msg_count = 0

    for _ in range(120):  # 2 minutes max
        r = httpx.get(f"{base}/v1/tasks/{task_id}", timeout=5)
        task = r.json()
        status = task["status"]

        # Get new messages
        r = httpx.get(f"{base}/v1/tasks/{task_id}/messages", timeout=5)
        msgs = r.json()

        for msg in msgs[last_msg_count:]:
            _print_message(msg)
        last_msg_count = len(msgs)

        if status in ("completed", "failed", "cancelled"):
            print(f"\nTask {status}.")
            if task.get("error"):
                print(f"Error: {task['error']}")
            break

        await asyncio.sleep(1)


def _print_event(event: dict):
    ts = datetime.now().strftime("%H:%M:%S")
    etype = event.get("type", "?")
    payload = event.get("payload", {})

    if etype == "task:message":
        msg_type = payload.get("type", payload.get("message_type", ""))
        content = payload.get("content", "")
        tool = payload.get("tool", "")

        if msg_type == "text":
            print(f"  [{ts}] 💬 {content[:200]}")
        elif msg_type == "tool_use":
            print(f"  [{ts}] 🔧 {tool}()")
        elif msg_type == "tool_result":
            output = payload.get("output", "")[:100]
            print(f"  [{ts}] ✓  {tool} → {output}")
        elif msg_type == "error":
            print(f"  [{ts}] ❌ {content[:200]}")
        else:
            print(f"  [{ts}] {msg_type}: {content[:100]}")

    elif etype == "task:created":
        print(f"  [{ts}] 📋 Task created: {payload.get('title', payload.get('task_id', ''))}")
    elif etype == "task:completed":
        print(f"  [{ts}] ✅ Task completed: {payload.get('task_id', '')}")
    elif etype == "task:failed":
        print(f"  [{ts}] ❌ Task failed: {payload.get('error', payload.get('task_id', ''))}")
    elif etype == "task:cancelled":
        print(f"  [{ts}] 🗑️  Task cancelled")
    elif etype == "task:progress":
        print(f"  [{ts}] 📊 {payload.get('status', '')}")
    else:
        print(f"  [{ts}] {etype}: {json.dumps(payload)[:100]}")


def _print_message(msg: dict):
    ts = msg.get("created_at", "")[-8:]
    msg_type = msg.get("type", "")
    content = msg.get("content", "")
    tool = msg.get("tool", "")

    if msg_type == "text":
        print(f"  [{ts}] 💬 {content[:200]}")
    elif msg_type == "tool_use":
        print(f"  [{ts}] 🔧 {tool}()")
    elif msg_type == "tool_result":
        output = msg.get("output", "")[:100]
        print(f"  [{ts}] ✓  {tool} → {output}")
    elif msg_type == "error":
        print(f"  [{ts}] ❌ {content[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
