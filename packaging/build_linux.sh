#!/usr/bin/env bash
# Build the Linux desktop app from source.
#
# Steps:
#   1. PyInstaller-bundle the Python server into a standalone onedir sidecar.
#   2. Stage that sidecar under the Tauri resources directory.
#   3. Run `tauri build` for Linux bundles.
#
# Prerequisites:
#   - Ubuntu/Debian native packages from `packaging/install_linux_desktop_deps.sh`.
#   - Rust via rustup.
#   - Node/npm and GUI dependencies (`npm ci` or `npm install` in `surfaces/gui`).
#   - The repo venv created by `packaging/setup_dev_env.sh`.
#
# Default bundles are `deb,appimage`. Override with:
#   packaging/build_linux.sh rpm
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
GUI="$ROOT/surfaces/gui"
VENV="$ROOT/.venv"
BUNDLES="${1:-deb,appimage}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command '$1' not found on PATH." >&2
    exit 1
  fi
}

require_pkg_config() {
  if ! pkg-config --exists "$1"; then
    echo "Missing pkg-config package '$1'. Run: packaging/install_linux_desktop_deps.sh" >&2
    exit 1
  fi
}

require_cmd rustc
require_cmd npm
require_cmd pkg-config
require_pkg_config libsoup-3.0
require_pkg_config webkit2gtk-4.1

if [ ! -x "$VENV/bin/pyinstaller" ]; then
  echo "PyInstaller not found at $VENV/bin/pyinstaller. Run: bash packaging/setup_dev_env.sh" >&2
  exit 1
fi

TRIPLE="$(rustc -vV | sed -n 's/host: //p')"

# Build the bundled whisper.cpp CPU backend for distribution, not for the
# build host. Some VMs expose AVX2 without FMA, which breaks ggml's AVX2 path,
# and native CPU flags can also produce binaries that fail on older Linux hosts.
export GGML_NATIVE="${GGML_NATIVE:-OFF}"
export GGML_AVX="${GGML_AVX:-OFF}"
export GGML_AVX2="${GGML_AVX2:-OFF}"
export GGML_AVX_VNNI="${GGML_AVX_VNNI:-OFF}"
export GGML_AVX512="${GGML_AVX512:-OFF}"
export GGML_AVX512_VBMI="${GGML_AVX512_VBMI:-OFF}"
export GGML_AVX512_VNNI="${GGML_AVX512_VNNI:-OFF}"
export GGML_AVX512_BF16="${GGML_AVX512_BF16:-OFF}"
export GGML_BMI2="${GGML_BMI2:-OFF}"
export GGML_F16C="${GGML_F16C:-OFF}"
export GGML_FMA="${GGML_FMA:-OFF}"
export GGML_SSE42="${GGML_SSE42:-OFF}"

echo "==> [1/3] PyInstaller: bundling openworker-server ($TRIPLE)"
"$VENV/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/3] staging sidecar resources"
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
cp -R "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/3] tauri build (--bundles $BUNDLES)"
( cd "$GUI" && npm run tauri build -- --bundles "$BUNDLES" )

BUNDLE="$GUI/src-tauri/target/release/bundle"
echo
echo "Done. Linux bundles under: $BUNDLE"
find "$BUNDLE" -type f \( -name '*.deb' -o -name '*.AppImage' -o -name '*.rpm' \) -print 2>/dev/null || true
