#!/usr/bin/env bash
# Build the Linux desktop app: an AppImage (runs on most distributions, auto-updates) and a
# .deb (Debian, Ubuntu and derivatives). The Linux counterpart of build_dmg.sh and
# build_windows.ps1. (build_linux.sh is something else: the headless `openworker` command.)
#
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot; it lands in
#      <prefix>/lib/OpenWorker/sidecar/ in both bundles (see server_bin() in src/lib.rs).
#   3. `tauri build --bundles appimage,deb` (resources are copied in).
#   4. With an updater key, make sure every bundle carries a minisign .sig.
#
# Build on the OLDEST distribution you want to support: the app and the frozen server link
# against the build machine's glibc and run only on that version or newer. Release CI builds
# on Ubuntu 22.04 (the oldest with webkit2gtk-4.1, which Tauri 2 needs). Natively per
# architecture (x86_64, aarch64): PyInstaller cannot cross-compile.
#
# Prerequisites:
#   - Rust (rustup) + Node/npm, and the GUI deps installed (npm ci in surfaces/gui).
#   - System packages (Debian/Ubuntu names):
#       sudo apt install build-essential curl wget file patchelf pkg-config libssl-dev \
#         libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev \
#         libasound2-dev cmake clang libclang-dev xdg-utils
#     (libasound2/cmake/clang: the voice-input crate — cpal and whisper.cpp — must compile
#     even though voice input is not offered on Linux yet.)
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3 -m venv .venv
#       .venv/bin/pip install -e '.[bedrock,openshell]' pyinstaller typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and
#     `mcp.cli` calls sys.exit() at import if typer is absent, which aborts the freeze.
#
# AUTO-UPDATE: set TAURI_SIGNING_PRIVATE_KEY (+ _PASSWORD), or put them in `.ocw-updater.env`
# one directory above the repo, to produce the signed updater artifacts. Without a key the
# bundles still build, but a release made from them cannot auto-update.
#
# Bundles: OCW_LINUX_BUNDLES=appimage,deb by default; add rpm for Fedora/openSUSE
# (needs rpmbuild): OCW_LINUX_BUNDLES=appimage,deb,rpm ./build_linux_app.sh
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
# the spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_linux_app.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
BUNDLES="${OCW_LINUX_BUNDLES:-appimage,deb}"

[ "$(uname -s)" = "Linux" ] || { echo "ERROR: this script builds on Linux only" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: '$1' not found on PATH — see the prerequisites in this script's header" >&2
    exit 1
  }
}
need rustc
need cargo
need npm
need node
need pkg-config
for pc in webkit2gtk-4.1 gtk+-3.0 alsa; do
  pkg-config --exists "$pc" || {
    echo "ERROR: development files for '$pc' are missing — see the apt line in this script's header" >&2
    exit 1
  }
done
[ -x "$PLATFORM/.venv/bin/pyinstaller" ] || {
  echo "ERROR: PyInstaller not found at .venv/bin/pyinstaller — create the venv (see header)" >&2
  exit 1
}
# OpenShell sandboxes talk to the gateway over gRPC; a sidecar without grpcio can never use them.
"$PLATFORM/.venv/bin/python" -c "import grpc" 2>/dev/null || {
  echo "ERROR: grpcio is missing from .venv; install the openshell extra (see header)" >&2
  exit 1
}

VERSION="$(node -p "require('$GUI/src-tauri/tauri.conf.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. x86_64-unknown-linux-gnu
echo "OpenWorker $VERSION for $TRIPLE (glibc $(ldd --version | head -1 | awk '{print $NF}'))"

echo "==> [1/4] PyInstaller: bundling openworker-server"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/4] staging sidecar resources"
# rm -rf first: cp writes THROUGH a symlink at the destination (see build_dmg.sh).
SIDECAR="$GUI/src-tauri/binaries/sidecar"
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$SIDECAR" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
# -L: Tauri's resource bundler copies files, not links; dereference now so what we test
# below is exactly what ships.
cp -RL "$HERE/dist/openworker-server" "$SIDECAR"
chmod +x "$SIDECAR/openworker-server"
# Start it once, against an empty state folder: a sidecar that cannot load its libraries
# must fail the build here, not on the user's machine.
COWORKER_STATE_DIR="$(mktemp -d)" "$SIDECAR/openworker-server" --check

echo "==> [3/4] tauri build (--bundles $BUNDLES)"
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
# linuxdeploy (used for the AppImage) is itself an AppImage; on machines and containers
# without FUSE it can only run extracted.
export APPIMAGE_EXTRACT_AND_RUN=1
# NO_STRIP: linuxdeploy's bundled strip is older than the toolchain on recent distributions
# and fails on their .relr.dyn sections; the sidecar's libraries are already release builds.
export NO_STRIP=true
( cd "$GUI" && npm run tauri build -- --bundles "$BUNDLES" ${UPDATER_OVERLAY[@]+"${UPDATER_OVERLAY[@]}"} )

BUNDLE="$GUI/src-tauri/target/release/bundle"

echo "==> [4/4] updater signatures"
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  # The updater installs by bundle type: an AppImage install swaps in a new AppImage, a .deb
  # install runs dpkg on a new .deb. Each needs its own signature; sign whatever tauri
  # did not already sign.
  for f in "$BUNDLE"/appimage/*.AppImage "$BUNDLE"/deb/*.deb "$BUNDLE"/rpm/*.rpm; do
    [ -f "$f" ] || continue
    [ -f "$f.sig" ] && continue
    echo "    signing $(basename "$f")"
    ( cd "$GUI" && npx --no-install tauri signer sign "$f" >/dev/null )
  done
else
  echo "    (skipped — no key)"
fi

echo ""
echo "Done. Bundles under: $BUNDLE"
find "$BUNDLE" -maxdepth 2 -type f \( -name "*.AppImage" -o -name "*.deb" -o -name "*.rpm" -o -name "*.sig" \) \
  -printf "  %p\n" | sort
