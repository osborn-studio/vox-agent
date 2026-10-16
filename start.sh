#!/bin/bash
# VoxLog2 quick start — runs both Python server and macOS app
set -e

VOXLOG_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$VOXLOG_ROOT"

# Activate venv
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "Error: .venv not found. Run: python3.13 -m venv .venv && pip install -e '.[dev]'"
    exit 1
fi

# Check .env
if [ ! -f ~/.voxlog2/.env ]; then
    echo "Error: ~/.voxlog2/.env not found. Copy .env.example and configure API keys."
    exit 1
fi

# Kill any existing VoxLog2 server
lsof -ti :7902 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

echo "Starting VoxLog2 server on localhost:7902..."
python -m uvicorn server.app:app --host 127.0.0.1 --port 7902 --log-level info &
SERVER_PID=$!

# Wait for server
for i in $(seq 1 10); do
    if curl -s http://127.0.0.1:7902/health > /dev/null 2>&1; then
        echo "Server ready."
        break
    fi
    sleep 1
done

echo "Starting VoxLog2 macOS app..."
VOXLOG_ROOT="$VOXLOG_ROOT" swift run --package-path macos/VoxLog 2>/dev/null &
APP_PID=$!

echo ""
echo "VoxLog2 is running!"
echo "  Server PID: $SERVER_PID"
echo "  App PID:    $APP_PID"
echo "  Press Right Option to record, release to paste."
echo "  Press Ctrl+C to stop."
echo ""

# Wait for either to exit
trap "kill $SERVER_PID $APP_PID 2>/dev/null; exit" INT TERM
wait
