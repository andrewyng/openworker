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

# `python3` isn't a recognized command on plain Windows (only `python` is); prefer
# python3 where it exists (macOS/Linux/WSL ship both, sometimes aliased) and fall
# back to python otherwise, so this line works unmodified in Git Bash too.
PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"

"$PY" -m venv "$VENV"

# venv layout differs by platform: POSIX (macOS/Linux/WSL) puts the interpreter
# and console scripts under bin/; native Windows Python — including when this
# script runs under Git Bash, which is the "Windows" path the README points
# people at — puts them under Scripts/. Detect rather than hardcode one, so the
# same script works on every "Run from source" path the README describes.
if [ -d "$VENV/Scripts" ]; then
  VBIN="$VENV/Scripts"
else
  VBIN="$VENV/bin"
fi

# The coworker package (server, engine, connectors) + inbound-messaging extras.
# aisuite comes in as a regular dependency (git-pinned in pyproject.toml until
# the next PyPI release).
"$VBIN/python" -m pip install --quiet --upgrade pip
cd "$ROOT"
"$VBIN/python" -m pip install --quiet -e ".[messaging,dev]"

"$VBIN/python" -c 'import aisuite, coworker' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $VBIN/openworker-server --cwd /path/to/your/project --port 8765"