#!/usr/bin/env bash
# Build a native Linux package. On DGX Spark this produces an ARM64 .deb containing the
# React/Tauri desktop shell and a self-contained PyInstaller server sidecar.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
GUI="$ROOT/surfaces/gui"
PYINSTALLER="$ROOT/.venv/bin/pyinstaller"

if [ "$(uname -s)" != "Linux" ]; then
  echo "ERROR: Linux packages must be built natively on Linux." >&2
  exit 1
fi
for command in rustc npm; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command '$command' was not found. See docs/dgx-spark.md." >&2
    exit 1
  fi
done
if [ ! -x "$PYINSTALLER" ]; then
  echo "ERROR: PyInstaller is missing. Run: bash packaging/setup_dgx_spark.sh" >&2
  exit 1
fi
if [ ! -d "$GUI/node_modules" ]; then
  echo "ERROR: GUI dependencies are missing. Run: bash packaging/setup_dgx_spark.sh" >&2
  exit 1
fi

TRIPLE="$(rustc -vV | sed -n 's/host: //p')"

echo "==> [1/3] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PYINSTALLER" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/3] Staging the server sidecar"
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar"
cp -a "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/3] Tauri: building a native .deb"
(cd "$GUI" && npm run tauri build -- --bundles deb)

echo
echo "Done. Package:"
find "$GUI/src-tauri/target/release/bundle/deb" -maxdepth 1 -type f -name '*.deb' -print

