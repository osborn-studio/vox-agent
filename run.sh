#!/bin/bash
# VoxLog — one command to rule them all
# Usage: ./run.sh
set -e
cd "$(dirname "$0")"

# Kill old stuff
killall VoxLog 2>/dev/null || true
lsof -ti :7890 | xargs kill -9 2>/dev/null || true
sleep 1

# Start server
source .venv/bin/activate
echo "Starting server..."
python -m uvicorn server.app:app --host 127.0.0.1 --port 7890 --log-level info &
SERVER_PID=$!
sleep 3

# Verify
if curl -s http://127.0.0.1:7890/health > /dev/null 2>&1; then
    echo "Server OK (PID $SERVER_PID)"
else
    echo "Server failed to start"
    exit 1
fi

# Start App
echo "Starting VoxLog..."
VOXLOG_ROOT="$(pwd)" swift run --package-path macos/VoxLog 2>/dev/null &
APP_PID=$!

echo ""
echo "==============================="
echo "  VoxLog is running!"
echo "  Server: localhost:7890"
echo "  Web UI: http://127.0.0.1:7890"
echo "  Ctrl+C to stop"
echo "==============================="

trap "kill $SERVER_PID $APP_PID 2>/dev/null; exit" INT TERM
wait
