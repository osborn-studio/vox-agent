#!/bin/bash
# Vox Agents development launcher
# Starts the Python engine and opens the SwiftUI app

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT=${VOX_ENGINE_PORT:-18900}

echo "╔══════════════════════════════════════╗"
echo "║       Vox Agents Dev Mode            ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 1. Ensure venv exists
if [ ! -d ".venv" ]; then
    echo "Creating venv..."
    python3 -m venv .venv
    .venv/bin/pip install -q fastapi uvicorn pydantic httpx aiosqlite
    echo "Dependencies installed."
fi

# 2. Start Python engine
echo "Starting engine on port $PORT..."
.venv/bin/python -m uvicorn engine.server:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --log-level info &
ENGINE_PID=$!

# Wait for startup
sleep 2
if curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then
    echo "✓ Engine running (PID $ENGINE_PID, port $PORT)"
else
    echo "✗ Engine failed to start"
    kill $ENGINE_PID 2>/dev/null
    exit 1
fi

# 3. Show status
echo ""
echo "API:       http://127.0.0.1:$PORT/docs"
echo "WebSocket: ws://127.0.0.1:$PORT/ws"
echo "Health:    http://127.0.0.1:$PORT/health"
echo ""

# 4. Build and run SwiftUI app (optional)
if [ "$1" = "--app" ]; then
    echo "Building SwiftUI app..."
    cd macos
    swift build 2>&1 | tail -3
    echo "Running VoxLog..."
    swift run VoxLog &
    APP_PID=$!
    cd ..
    echo "✓ App running (PID $APP_PID)"
fi

# 5. Trap exit to clean up
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $ENGINE_PID 2>/dev/null
    [ -n "$APP_PID" ] && kill $APP_PID 2>/dev/null
    echo "Done."
}
trap cleanup EXIT

echo ""
echo "Press Ctrl+C to stop."
echo ""

# Keep running
wait $ENGINE_PID
