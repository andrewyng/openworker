#!/usr/bin/env bash
# Prepare a DGX Spark checkout for the browser UI and native desktop builds.
#
# This intentionally installs only repository-local Python and Node dependencies. System
# packages stay an explicit apt step (documented in docs/dgx-spark.md) so this script never
# asks for sudo or changes the DGX OS installation behind the user's back.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI="$ROOT/surfaces/gui"
VENV="$ROOT/.venv"

if [ "$(uname -s)" != "Linux" ]; then
  echo "ERROR: DGX Spark setup must run on Linux (found $(uname -s))." >&2
  exit 1
fi
case "$(uname -m)" in
  aarch64|arm64) ;;
  *)
    echo "ERROR: DGX Spark requires ARM64/aarch64 (found $(uname -m))." >&2
    exit 1
    ;;
esac

for command in python3 node npm git; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command '$command' was not found. See docs/dgx-spark.md." >&2
    exit 1
  fi
done

python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
node -e 'const major=Number(process.versions.node.split(".")[0]); if (major < 20) { console.error("Node 20+ is required"); process.exit(1) }'

echo "==> Creating the Python environment"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install -e "$ROOT[messaging,dev,bedrock]" pyinstaller typer
"$VENV/bin/python" -c 'import aisuite, coworker'

echo "==> Installing the GUI dependencies"
(cd "$GUI" && npm ci)

echo
echo "DGX Spark setup is ready."
echo "  Browser UI: bash packaging/run_dgx_spark.sh"
echo "  Desktop .deb: bash packaging/build_linux.sh"

