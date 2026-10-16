#!/usr/bin/env python3
"""Vox Agents Engine CLI — start, status, and task management."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def _error_body(r: httpx.Response):
    """Best-effort error detail for a non-200 response.

    Prefers the parsed JSON body (most engine error responses are JSON), but
    falls back to raw text if the body isn't valid JSON — e.g. a 500 with an
    HTML/plain-text error page — so callers never crash on `.json()`.
    """
    try:
        return r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"


def main():
    parser = argparse.ArgumentParser(
        prog="vox-engine",
        description="Vox Agents Engine — manage agent tasks from the command line",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    start_p = sub.add_parser("start", help="Start the engine server")
    start_p.add_argument("--port", type=int, default=18900)
    start_p.add_argument("--host", default="127.0.0.1")

    # status
    sub.add_parser("status", help="Check engine health")

    # task create
    task_p = sub.add_parser("task", help="Create a task")
    task_p.add_argument("prompt", help="Task prompt / instruction")
    task_p.add_argument("--agent", "-a", default="claude-code", help="Agent ID")
    task_p.add_argument("--title", "-t", help="Task title (default: first 50 chars of prompt)")
    task_p.add_argument("--priority", "-p", type=int, default=1, choices=[0, 1, 2, 3])
    task_p.add_argument("--provider", default="claude")

    # task list
    list_p = sub.add_parser("tasks", help="List tasks")
    list_p.add_argument("--agent", "-a", help="Filter by agent ID")
    list_p.add_argument("--status", "-s", help="Filter by status")

    # task cancel
    cancel_p = sub.add_parser("cancel", help="Cancel a task")
    cancel_p.add_argument("task_id", help="Task ID to cancel")

    # tools
    sub.add_parser("tools", help="List registered tools")

    # skills
    sub.add_parser("skills", help="List loaded skills")

    # agents
    sub.add_parser("agents", help="List registered agents")

    # stats
    sub.add_parser("stats", help="Show task execution statistics")

    # export
    export_p = sub.add_parser("export", help="Export task history to Obsidian")
    export_p.add_argument("--days", type=int, default=7, help="Number of days to export")

    # watch (WebSocket client)
    watch_p = sub.add_parser("watch", help="Watch real-time engine events")
    watch_p.add_argument("--task", help="Create a task and watch execution")
    watch_p.add_argument("--agent", "-a", default="claude-code")

    # memory (VOXAGENT mobile companion voice-memory API)
    memory_p = sub.add_parser("memory", help="Search and browse voice-memory nodes")
    memory_sub = memory_p.add_subparsers(dest="memory_command", required=True)

    memory_search_p = memory_sub.add_parser("search", help="Search memory nodes")
    memory_search_p.add_argument("query", help="Search query")
    memory_search_p.add_argument("--limit", type=int, default=10)
    memory_search_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    memory_recent_p = memory_sub.add_parser("recent", help="List recent memory nodes")
    memory_recent_p.add_argument("--days", type=int, default=7)
    memory_recent_p.add_argument("--limit", type=int, default=20)
    memory_recent_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    # device (VOXAGENT simulated device protocol)
    device_p = sub.add_parser("device", help="Pair and control VOXAGENT capture devices")
    device_sub = device_p.add_subparsers(dest="device_command", required=True)

    device_sub.add_parser("pairing-code", help="Generate a device pairing code").add_argument(
        "--pretty", action="store_true", help="Human-readable summary"
    )

    device_pair_p = device_sub.add_parser("pair", help="Pair a device with a pairing code")
    device_pair_p.add_argument("code", help="Pairing code")
    device_pair_p.add_argument("--owner", default="user_1", help="Owner user ID")
    device_pair_p.add_argument("--workspace", default="ws_1", help="Workspace ID")
    device_pair_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    device_sim_p = device_sub.add_parser(
        "simulate-record", help="Simulate a record-button press on a paired device"
    )
    device_sim_p.add_argument("device_id", help="Device ID")
    device_sim_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    # capture (VOXAGENT capture session processing)
    capture_p = sub.add_parser("capture", help="Process VOXAGENT capture sessions")
    capture_sub = capture_p.add_subparsers(dest="capture_command", required=True)

    capture_process_p = capture_sub.add_parser("process", help="Process a capture session (ASR)")
    capture_process_p.add_argument("session_id", help="Capture session ID")
    capture_process_p.add_argument("--audio", help="Path to the audio file to transcribe")
    capture_process_p.add_argument("--asr", help="ASR adapter name (e.g. mock, sherpa)")
    capture_process_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    capture_upload_p = capture_sub.add_parser(
        "upload", help="Upload an audio file to a capture session"
    )
    capture_upload_p.add_argument("session_id", help="Capture session ID")
    capture_upload_p.add_argument("--file", "-f", required=True, help="Path to the audio file")
    capture_upload_p.add_argument("--pretty", action="store_true", help="Human-readable summary")

    # tokens (VOXAGENT token ledger)
    tokens_p = sub.add_parser("tokens", help="Check the local token balance and ledger")
    tokens_sub = tokens_p.add_subparsers(dest="tokens_command", required=True)
    tokens_sub.add_parser("balance", help="Show current token balance").add_argument(
        "--pretty", action="store_true", help="Human-readable summary"
    )

    # serve-mcp (VOXAGENT MCP tool server)
    sub.add_parser("serve-mcp", help="Start the VOXAGENT MCP tool server")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "start":
        _start_server(args)
    else:
        _run_client_command(args)


def _start_server(args):
    import uvicorn
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    print(f"Starting Vox Agents Engine on {args.host}:{args.port}")
    print(f"  WebSocket: ws://{args.host}:{args.port}/ws")
    print(f"  REST API:  http://{args.host}:{args.port}/v1/")
    print(f"  Docs:      http://{args.host}:{args.port}/docs")
    print()

    from engine.server import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


def _run_client_command(args):
    base = "http://127.0.0.1:18900"

    try:
        if args.command == "status":
            r = httpx.get(f"{base}/health", timeout=3)
            data = r.json()
            print(f"Engine: {data['status']}")
            print(f"Active tasks: {data.get('active_tasks', 0)}")

        elif args.command == "task":
            title = args.title or args.prompt[:50]
            r = httpx.post(f"{base}/v1/tasks", json={
                "agent_id": args.agent,
                "title": title,
                "prompt": args.prompt,
                "priority": args.priority,
                "provider": args.provider,
            }, timeout=10)
            data = r.json()
            print(f"Task created: {data['task_id']}")
            print(f"Status: {data['status']}")
            print(f"Agent: {args.agent}")

        elif args.command == "tasks":
            params = {}
            if getattr(args, "agent", None):
                params["agent_id"] = args.agent
            if getattr(args, "status", None):
                params["status"] = args.status
            r = httpx.get(f"{base}/v1/tasks", params=params, timeout=5)
            tasks = r.json()
            if not tasks:
                print("No tasks found.")
                return
            for t in tasks:
                emoji = {"draft": "📝", "running": "🔄", "completed": "✅",
                         "failed": "❌", "cancelled": "🗑️"}.get(t["status"], "📋")
                print(f"  {emoji} [{t['id'][:8]}] {t['title']} → {t['agent_id']} ({t['status']})")

        elif args.command == "cancel":
            r = httpx.post(f"{base}/v1/tasks/{args.task_id}/cancel", timeout=5)
            if r.status_code == 200:
                print(f"Task {args.task_id} cancelled.")
            else:
                print(f"Cannot cancel: {r.json()}")

        elif args.command == "tools":
            r = httpx.get(f"{base}/v1/tools", timeout=3)
            for t in r.json():
                print(f"  {t['name']:15s} {t['description']}")

        elif args.command == "skills":
            r = httpx.get(f"{base}/v1/skills", timeout=3)
            skills = r.json()
            if not skills:
                print("No skills loaded.")
                return
            for s in skills:
                tools = ", ".join(s.get("tools", []))
                print(f"  {s['name']:20s} {s['description'][:50]}  [{tools}]")

        elif args.command == "agents":
            r = httpx.get(f"{base}/v1/agents", timeout=3)
            agents = r.json()
            if not agents:
                print("No agents found. Check ~/.osborn/config/agents/")
                return
            for a in agents:
                parent = f" (child of {a['parent_id']})" if a.get("parent_id") else ""
                ext = f" [{a['external_system']}]" if a.get("external_system") else ""
                print(f"  {a['emoji']} {a['name']:20s} {a['provider']:10s}{parent}{ext}")

        elif args.command == "stats":
            r = httpx.get(f"{base}/v1/analytics", timeout=5)
            data = r.json()
            print(f"Tasks: {data['total_tasks']} total | {data['completed']} completed | {data['failed']} failed")
            print(f"Today: {data['tasks_today']} | This week: {data['tasks_this_week']}")
            print(f"Active: {data['active']}")
            if data.get("agents"):
                print("\nPer agent:")
                for a in data["agents"]:
                    rate = f"{a['success_rate']*100:.0f}%" if a['total_tasks'] > 0 else "—"
                    dur = f"{a['avg_duration_sec']:.1f}s" if a['avg_duration_sec'] > 0 else "—"
                    print(f"  {a['agent_id']:20s} {a['total_tasks']:3d} tasks  {rate:>5s} success  {dur:>8s} avg")

        elif args.command == "export":
            r = httpx.post(f"{base}/v1/export/obsidian",
                           params={"days": args.days}, timeout=30)
            data = r.json()
            print(f"Exported {data['files_written']} files ({data['days']} days)")
            print("Target: ~/obsidian-vault/06-osborn/agent-logs/")

        elif args.command == "watch":
            import asyncio as _aio
            from engine.ws_client import main as ws_main
            # Re-parse with ws_client's parser
            sys.argv = ["ws_client", "--port", "18900"]
            if args.task:
                sys.argv += ["--task", args.task, "--agent", args.agent]
            _aio.run(ws_main())

        elif args.command == "memory":
            _run_memory_command(args, base)

        elif args.command == "device":
            _run_device_command(args, base)

        elif args.command == "capture":
            _run_capture_command(args, base)

        elif args.command == "tokens":
            _run_tokens_command(args, base)

        elif args.command == "serve-mcp":
            _run_serve_mcp()

    except httpx.ConnectError:
        print("Engine not running. Start with: vox-engine start")
        sys.exit(1)


def _run_memory_command(args, base: str) -> None:
    if args.memory_command == "search":
        r = httpx.get(f"{base}/v1/memory/search", params={"q": args.query, "limit": args.limit}, timeout=5)
        if r.status_code != 200:
            print(f"Search failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            results = data.get("results", [])
            if not results:
                print("No matches.")
                return
            for item in results:
                node = item.get("node", {})
                print(f"  [{node.get('id', '')[:12]}] {node.get('title', '')}  (score={item.get('score')})")
        else:
            print(json.dumps(data))

    elif args.memory_command == "recent":
        r = httpx.get(f"{base}/v1/memory/recent", params={"days": args.days, "limit": args.limit}, timeout=5)
        if r.status_code != 200:
            print(f"Recent lookup failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            if not data:
                print("No recent memory nodes.")
                return
            for node in data:
                print(f"  [{node.get('id', '')[:12]}] {node.get('title', '')}  ({node.get('createdAt', '')})")
        else:
            print(json.dumps(data))


def _run_device_command(args, base: str) -> None:
    if args.device_command == "pairing-code":
        r = httpx.post(f"{base}/v1/devices/pairing-codes", timeout=5)
        if r.status_code != 200:
            print(f"Pairing code request failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Pairing code: {data['code']}  (expires {data.get('expiresAt', '')})")
            print(f"QR payload: {data.get('qrPayload', '')}")
        else:
            print(json.dumps(data))

    elif args.device_command == "pair":
        r = httpx.post(
            f"{base}/v1/devices/pair",
            json={"code": args.code, "ownerUserId": args.owner, "workspaceId": args.workspace},
            timeout=5,
        )
        if r.status_code != 200:
            print(f"Pairing failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Paired device {data['id']}  status={data.get('status')}")
        else:
            print(json.dumps(data))

    elif args.device_command == "simulate-record":
        r = httpx.post(
            f"{base}/v1/devices/{args.device_id}/events",
            json={"type": "RECORD_BUTTON_PRESSED", "payload": {}},
            timeout=5,
        )
        if r.status_code != 200:
            print(f"Event failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Event {data.get('id')}  session={data.get('sessionId')}")
        else:
            print(json.dumps(data))


def _run_capture_command(args, base: str) -> None:
    if args.capture_command == "process":
        body = {}
        if args.audio:
            body["audioPath"] = args.audio
        if args.asr:
            body["asrAdapter"] = args.asr
        r = httpx.post(
            f"{base}/v1/capture/sessions/{args.session_id}/process",
            json=body,
            timeout=30,
        )
        if r.status_code != 200:
            print(f"Process failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Session {data.get('id')}  status={data.get('status')}")
            print(f"Detected languages: {', '.join(data.get('detectedLanguages', []))}")
        else:
            print(json.dumps(data))

    elif args.capture_command == "upload":
        path = Path(args.file)
        if not path.is_file():
            print(f"Audio file not found: {args.file}")
            sys.exit(1)
        with path.open("rb") as fh:
            r = httpx.post(
                f"{base}/v1/capture/sessions/{args.session_id}/audio",
                files={"file": (path.name, fh, "audio/wav")},
                timeout=60,
            )
        if r.status_code != 200:
            print(f"Upload failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Uploaded {data.get('bytes')} bytes → {data.get('audioPath')}")
            print(f"Next: vox-engine capture process {data.get('sessionId')} --asr sherpa")
        else:
            print(json.dumps(data))


def _run_tokens_command(args, base: str) -> None:
    if args.tokens_command == "balance":
        r = httpx.get(f"{base}/v1/tokens/balance", timeout=5)
        if r.status_code != 200:
            print(f"Balance lookup failed: {_error_body(r)}")
            sys.exit(1)
        data = r.json()
        if args.pretty:
            print(f"Balance: {data.get('balance')} {data.get('currency', '')}")
        else:
            print(json.dumps(data))


def _run_serve_mcp() -> None:
    from voxagent_mcp.voxagent_server import serve

    try:
        serve()
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
