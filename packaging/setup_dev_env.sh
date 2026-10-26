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

# Pick the newest Python >= 3.10 on PATH. The python3 that ships with macOS
# (and some Linux images) can be older than that, so try the versioned names
# first and fall back to bare python3.
PYTHON=""
found=()
probe='import sys; print("%d.%d.%d" % sys.version_info[:3]); sys.exit(0 if sys.version_info >= (3, 10) else 1)'
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  path="$(command -v "$candidate" 2>/dev/null)" || continue
  if version="$("$path" -c "$probe" 2>/dev/null)"; then
    PYTHON="$path"
    break
  fi
  found+=("$path (${version:-not runnable})")
done

if [ -z "$PYTHON" ]; then
  echo "Error: Python 3.10 or newer is required, but none was found on PATH." >&2
  if [ "${#found[@]}" -gt 0 ]; then
    echo "Checked:" >&2
    printf '  %s\n' "${found[@]}" >&2
  fi
  echo "Install one (macOS: brew install python@3.13) or add it to PATH, then rerun." >&2
  exit 1
fi

echo "Using $PYTHON ($version)"
"$PYTHON" -m venv "$VENV"
# The coworker package (server, engine, connectors) + inbound-messaging extras.
# aisuite comes in as a regular dependency (git-pinned in pyproject.toml until
# the next PyPI release).
"$VENV/bin/pip" install --quiet --upgrade pip
# Keep the extras in step with CI (.github/workflows/ci.yml) — the bedrock test
# suite imports boto3 directly, so skipping that extra leaves a fresh env red.
"$VENV/bin/pip" install --quiet -e "$ROOT[messaging,dev,bedrock]"

"$VENV/bin/python" -c 'import aisuite, coworker' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $VENV/bin/openworker-server --cwd /path/to/your/project --port 8765"
