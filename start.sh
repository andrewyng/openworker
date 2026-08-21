#!/usr/bin/env bash
# Foreground OpenWorker for development: agent server + Vite UI in this terminal.
#
# Day to day you do not need this — both run as systemd user services
# (openworker-server.service, openworker-ui.service) and the desktop launcher just opens
# a window. Use this when you want the logs in front of you; it refuses to start on top
# of the services rather than fighting them for the ports.
#
# Usage: ./start.sh [workspace-dir]     default workspace: ~/openworker-workspace
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${1:-$HOME/openworker-workspace}"
# Vite hardcodes sidecar-8765.token, so the server port is not configurable here.
PORT=8765
STATE="${COWORKER_STATE_DIR:-$HOME/.config/coworker}"

if curl -sf -m 2 -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null ||
   ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT "; then
  echo "openworker-server is already listening on $PORT."
  echo "  stop the service first:  systemctl --user stop openworker-server"
  echo "  or just use it:          http://127.0.0.1:1420"
  exit 1
fi

mkdir -p "$WORKSPACE"

# Same token and PATH handling as the service wrapper, so a dev run and a service run are
# interchangeable. See ~/.local/bin/openworker-serve for why the token is pinned.
TOKEN_FILE="$STATE/sidecar-$PORT.token"
mkdir -p "$STATE"
if [ ! -s "$TOKEN_FILE" ]; then
  (umask 077; "$ROOT/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))' >"$TOKEN_FILE")
fi
COWORKER_API_TOKEN="$(cat "$TOKEN_FILE")"
export COWORKER_API_TOKEN

DEEPAGENTS_ENV="$HOME/.deepagents/.env"
if [ -f "$DEEPAGENTS_ENV" ]; then
  eval "$(grep -E '^QDRANT_URL=' "$DEEPAGENTS_ENV" | sed 's/^/export /')"
fi

"$ROOT/.venv/bin/openworker-server" --cwd "$WORKSPACE" --port "$PORT" &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 40); do
  ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT " && break
  sleep 0.25
done

# Not exec'd: the EXIT trap has to survive to stop the server when the UI quits.
cd "$ROOT/surfaces/gui"
npm run dev
