#!/usr/bin/env bash
# Build the OpenWorker Linux desktop app: a .deb and an AppImage.
#
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot.
#   3. `tauri build --bundles deb,appimage` → installable packages (resources copied in).
#
# The Linux counterpart to build_dmg.sh / build_windows.ps1. It is UNSIGNED — Linux has no
# universal code-signing scheme; distro repos / AppArmor / GPG-signing the release artifacts
# are downstream concerns.
#
# Prerequisites (mirrors the macOS/Windows scripts' headers):
#   - Rust (rustup) + Node/npm, and the GUI deps installed (`npm ci` in surfaces/gui).
#   - Tauri Linux system libraries (Debian/Ubuntu names):
#       sudo apt-get install -y libwebkit2gtk-4.1-dev libssl-dev libayatana-appindicator3-dev \
#         librsvg2-dev patchelf build-essential pkg-config file libclang-dev
#     (Fedora: webkit2gtk4.1-devel libayatana-appindicator-devel librsvg2-devel patchelf clang-devel;
#      Arch: webkit2gtk-4.1 libayatana-appindicator librsvg patchelf clang)
#     The STT sidecar (ocw-stt) uses cpal → ALSA (libasound2-dev) and whisper.cpp needs a C/C++
#     compiler (build-essential / gcc-c++) plus libclang-dev for bindgen at build time.
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3 -m venv .venv
#       .venv/bin/pip install -e '.[bedrock]' pyinstaller typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and `mcp.cli`
#     calls sys.exit() at import if typer is absent, which aborts the freeze.
#     (aisuite installs like any other dependency — git-pinned in pyproject.toml.)
#   - AppImage: Tauri fetches its own `linuxdeploy`/`appimagetool` at build time, so a network
#     connection is required for the appimage target. Running an AppImage needs `libfuse2`.
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default — the
# spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_linux.sh
#
# Override the bundles with BUNDLES= (e.g. BUNDLES=deb or BUNDLES=rpm,appimage). `rpm` requires
# `rpm`/`rpmbuild` to be installed; Tauri only emits .rpm when it is requested explicitly.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
APP="OpenWorker"
# Single source of truth for the version: tauri.conf.json (also stamps the bundle).
VERSION="$(node -p "require('$GUI/src-tauri/tauri.conf.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. x86_64-unknown-linux-gnu
ARCH="${TRIPLE%%-*}"
BUNDLES="${BUNDLES:-deb,appimage}"

# A running openworker-server (e.g. a prior dev sidecar) can hold a write lock on the PyInstaller
# output and make the overwrite fail. Best-effort: stop any before bundling.
if pgrep -x openworker-server >/dev/null 2>&1; then
  echo "==> stopping running openworker-server process(es) holding the output exe"
  pkill -x openworker-server || true
  sleep 1
fi

echo "==> [1/3] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/3] staging sidecar resources"
# Onedir bundle (exe + _internal/) ships via Tauri `resources`, landing under the install
# resource dir (e.g. /usr/lib/<productName>/sidecar/ on a deb). rm -rf first: a dev-convenience
# symlink here once clobbered another worktree's venv; also clears stale pre-onedir onefile bins.
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
cp -RL "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/3] tauri build (--bundles $BUNDLES)"
# Auto-update artifacts (.deb/.AppImage + minisign .sig): produced only when the updater
# signing key is available — from the env (CI secret TAURI_SIGNING_PRIVATE_KEY), or from
# `.ocw-updater.env` one directory above the repo (same convention as the macOS script).
# Keyless builds skip the overlay entirely so dev/fork builds keep working; a keyless RELEASE
# would strand every install without auto-update, hence the loud warning.
UPDATER_ENV="${OCW_UPDATER_ENV:-$PLATFORM/../.ocw-updater.env}"
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ] && [ -f "$UPDATER_ENV" ]; then
  # shellcheck disable=SC1090
  source "$UPDATER_ENV"
fi
UPDATER_OVERLAY=()
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  UPDATER_OVERLAY=(--config '{"bundle":{"createUpdaterArtifacts":true}}')
else
  echo "    WARNING: no updater signing key — building WITHOUT auto-update artifacts (not releasable)."
fi
# ${arr[@]+…} guard: plain "${arr[@]}" on an EMPTY array is "unbound variable" under set -u.
( cd "$GUI" && npm run tauri build -- --bundles "$BUNDLES" ${UPDATER_OVERLAY[@]+"${UPDATER_OVERLAY[@]}"} )

BUNDLE="$GUI/src-tauri/target/release/bundle"
echo ""
echo "Done. Bundles under: $BUNDLE"
# List whatever was actually produced (each target dir may or may not exist).
find "$BUNDLE" \( -name '*.deb' -o -name '*.rpm' -o -name '*.AppImage' \) -print 2>/dev/null || true
