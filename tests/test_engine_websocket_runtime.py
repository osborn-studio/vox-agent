"""Exercise a real Uvicorn WebSocket upgrade, not TestClient's in-process shim."""

from __future__ import annotations

import asyncio
import json
import socket

import uvicorn
from websockets.asyncio.client import connect

from engine.app_context import create_app_context
from engine.server import create_app


async def test_installed_server_accepts_and_delivers_engine_events(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
    )
    application = create_app(context)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(application, log_level="error", lifespan="on")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started

        async with connect(f"ws://127.0.0.1:{port}/ws") as websocket:
            for _ in range(100):
                if context.event_bus._ws_queues:
                    break
                await asyncio.sleep(0.01)
            await context.event_bus.emit(
                "task:progress",
                {"task_id": "runtime-smoke", "status": "running"},
            )
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))

        assert event == {
            "type": "task:progress",
            "payload": {"task_id": "runtime-smoke", "status": "running"},
        }
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
