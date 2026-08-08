#!/usr/bin/env bash
# Build the Linux desktop app + AppImage and .deb package.
#
# The Linux counterpart to build_dmg.sh / build_windows.ps1:
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot.
#   3. `tauri build --bundles appimage,deb` -> OpenWorker .AppImage + .deb (resources copied in).
#
# Prerequisites (mirrors build_windows.ps1's header):
#   - Rust (rustup) + Node/npm, and the GUI deps installed (npm ci in surfaces/gui).
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3 -m venv .venv
#       .venv/bin/pip install -e . pyinstaller tzdata typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and
#     `mcp.cli` calls sys.exit() at import if typer is absent, which aborts the freeze.
#     (aisuite installs like any other dependency - git-pinned in pyproject.toml.)
#   - Linux system deps for the Tauri bundlers (Debian/Ubuntu package names):
#       build-essential libssl-dev libgtk-3-dev libwebkit2gtk-4.1-dev libappindicator3-dev
#       librsvg2-dev patchelf libfuse2 fakeroot dpkg desktop-file-utils
#
# The result is UNSIGNED - Tauri's updater signing (minisign) still applies if
# TAURI_SIGNING_PRIVATE_KEY is set, same as the other platform scripts; there is no OS-level
# code signing on Linux for AppImage/.deb.
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default -
# the spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_linux.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
APP="OpenWorker"
VPY="$PLATFORM/.venv/bin/python"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. x86_64-unknown-linux-gnu

echo "==> [1/3] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/3] staging sidecar resources"
# Onedir bundle (exe + _internal/) ships via Tauri `resources`, landing at sidecar/ next to
# the app binary - onefile's per-launch self-extraction cost seconds of boot splash.
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
cp -r "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/3] tauri build (--bundles appimage,deb)"
# Auto-update artifact (.AppImage + minisign .sig): produced only when the updater signing
# key is available (CI secret TAURI_SIGNING_PRIVATE_KEY). Keyless builds skip the overlay so
# dev/fork builds keep working; keyless RELEASES strand Linux installs without auto-update.
UPDATER_OVERLAY=()
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  UPDATER_OVERLAY=(--config '{"bundle":{"createUpdaterArtifacts":true}}')
else
  echo "    WARNING: no updater signing key - building WITHOUT auto-update artifacts (not releasable)."
fi
# ${arr[@]+…} guard: plain "${arr[@]}" on an EMPTY array is an "unbound variable" under
# set -u on bash < 4.4 (see build_dmg.sh) - keep the same guard for consistency.
( cd "$GUI" && npm run tauri build -- --bundles appimage,deb ${UPDATER_OVERLAY[@]+"${UPDATER_OVERLAY[@]}"} )

BUNDLE="$GUI/src-tauri/target/release/bundle"
echo ""
echo "Done."
echo "AppImage: $BUNDLE/appimage/"
echo ".deb:     $BUNDLE/deb/"
