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

python3 -m venv "$VENV"
# venv puts executables in Scripts/ on Windows (Git Bash) and bin/ on Unix/WSL.
if [ -d "$VENV/Scripts" ]; then BIN="$VENV/Scripts"; else BIN="$VENV/bin"; fi

# The coworker package (server, engine, connectors) + inbound-messaging extras.
# aisuite comes in as a regular dependency (git-pinned in pyproject.toml until
# the next PyPI release).
# Use `python -m pip` (not the pip launcher) so pip can upgrade itself on
# Windows, where the running pip.exe is locked against modification.
"$BIN/python" -m pip install --quiet --upgrade pip
# Install from "." inside $ROOT rather than an absolute path: under Git Bash the
# MSYS-style $ROOT (/h/...) is not resolvable by the native Windows pip.
(cd "$ROOT" && "$BIN/python" -m pip install --quiet -e ".[messaging,dev]")

"$BIN/python" -c 'import aisuite, coworker' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $BIN/openworker-server --cwd /path/to/your/project --port 8765"
