#!/usr/bin/env bash
# Run OpenWorker's server and browser UI together on DGX Spark.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI="$ROOT/surfaces/gui"
SERVER="$ROOT/.venv/bin/openworker-server"
WORKSPACE="${1:-$HOME}"
SERVER_PID=""

if [ ! -x "$SERVER" ] || [ ! -d "$GUI/node_modules" ]; then
  echo "ERROR: setup is incomplete. Run: bash packaging/setup_dgx_spark.sh" >&2
  exit 1
fi

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "==> Starting OpenWorker server for workspace: $WORKSPACE"
"$SERVER" --cwd "$WORKSPACE" --host 127.0.0.1 --port 8765 &
SERVER_PID=$!

# Vite reads the freshly-created launch token when it starts. Waiting for health also gives a
# clear early failure instead of launching a UI that can never authenticate to its server.
for _ in $(seq 1 100); do
  if curl -fsS http://127.0.0.1:8765/v1/health >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    wait "$SERVER_PID"
    exit 1
  fi
  sleep 0.1
done
if ! curl -fsS http://127.0.0.1:8765/v1/health >/dev/null 2>&1; then
  echo "ERROR: OpenWorker server did not become ready on port 8765." >&2
  exit 1
fi

echo "==> Open http://127.0.0.1:1420"
echo "    Over SSH, forward both ports:"
echo "    ssh -L 1420:127.0.0.1:1420 -L 8765:127.0.0.1:8765 <user>@<spark>"
(cd "$GUI" && npm run dev -- --host 127.0.0.1)

