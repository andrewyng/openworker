#!/usr/bin/env bash
# One-time dev bootstrap for a fresh checkout: creates the Python venv every
# from-source flow expects at .venv — the browser dev flow runs its
# openworker-server directly, and the Tauri desktop shell falls back to it when
# no packaged sidecar binary is present (src-tauri/src/lib.rs, resolution step 3).
#
# Usage: bash packaging/setup_dev_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"

# macOS ships Python 3.9 which is too old — probe for a recent interpreter
# or use `uv` (the fast Rust venv tool) which manages its own Python version.
if command -v uv &>/dev/null; then
  # uv creates the venv using whatever Python it manages (3.12+ guaranteed)
  uv venv "$VENV"
elif command -v python3.13 &>/dev/null; then
  python3.13 -m venv "$VENV"
elif command -v python3.12 &>/dev/null; then
  python3.12 -m venv "$VENV"
elif command -v python3.11 &>/dev/null; then
  python3.11 -m venv "$VENV"
elif command -v python3.10 &>/dev/null; then
  python3.10 -m venv "$VENV"
else
  # fallback — will fail on macOS 3.9.x with a clear error
  python3 -m venv "$VENV"
fi
# The coworker package (server, engine, connectors) + inbound-messaging extras.
# aisuite comes in as a regular dependency (git-pinned in pyproject.toml until
# the next PyPI release).
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT[messaging,dev]"

"$VENV/bin/python" -c 'import aisuite, coworker' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $VENV/bin/openworker-server --cwd /path/to/your/project --port 8765"
