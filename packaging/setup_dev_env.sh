#!/usr/bin/env bash
# One-time dev bootstrap for a fresh checkout: creates the Python venv every
# from-source flow expects at platform/.venv — the browser dev flow runs its
# coworker-server directly, and the Tauri desktop shell falls back to it when
# no packaged sidecar binary is present (src-tauri/src/lib.rs, resolution step 3).
#
# Usage: bash platform/packaging/setup_dev_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$ROOT/platform/.venv"

python3 -m venv "$VENV"
# The coworker package (server, engine, connectors) + inbound-messaging extras.
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT/platform[messaging,dev]"

# `import aisuite` resolves from THIS checkout, not PyPI — a .pth puts the repo
# root on the venv's path (the packaged app freezes it the same way).
SITE="$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
echo "$ROOT" > "$SITE/aisuite_src.pth"

"$VENV/bin/python" -c 'import aisuite, coworker' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $VENV/bin/coworker-server --cwd /path/to/your/project --port 8765"
