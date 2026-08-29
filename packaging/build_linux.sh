#!/usr/bin/env bash
# Build the Linux desktop app: a .deb and an .AppImage.
#
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot.
#   3. `tauri build --bundles deb,appimage` → OpenWorker_<version>_<arch>.deb + .AppImage.
#
# Two formats because they answer different questions:
#   .deb       — what to install on Debian/Ubuntu and on ChromeOS Crostini (double-click in
#                the Files app). Upgrades come from the package manager, so the in-app updater
#                stays quiet (see `self_update_supported` in src-tauri/src/lib.rs).
#   .AppImage  — one portable file, no root, self-updating. Needs FUSE; where that is missing
#                (Crostini included) it still runs via `--appimage-extract-and-run`.
#
# Prerequisites (mirrors build_dmg.sh's header):
#   - Rust (rustup) + Node/npm, and the GUI deps installed (npm ci in surfaces/gui).
#   - The Tauri system libraries. On Debian/Ubuntu:
#       sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
#                        libayatana-appindicator3-dev patchelf file
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3 -m venv .venv
#       .venv/bin/pip install -e '.[bedrock]' pyinstaller typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and
#     `mcp.cli` calls sys.exit() at import if typer is absent, which aborts the freeze.
#
# GLIBC REACH: a frozen sidecar and a Rust binary both link the build machine's glibc, and
# glibc is forward- but not backward-compatible. Build on the OLDEST distro you intend to
# support — release CI uses ubuntu-22.04 (glibc 2.35) so the artifacts also run on Debian 12
# (glibc 2.36), which is what Crostini ships. Building on Ubuntu 24.04 produces artifacts
# that will NOT start there.
#
# AUTO-UPDATE: the .AppImage updater artifacts (.AppImage.tar.gz + minisign .sig) are produced
# only when the updater signing key is available — from the env (CI secret
# TAURI_SIGNING_PRIVATE_KEY), or from `.ocw-updater.env` one directory above the repo (same
# convention as build_dmg.sh). Keyless builds skip them so fork/dev builds keep working.
#
# There is no code signing on Linux; nothing here is the equivalent of Apple notarization.
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
# the spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_linux.sh
#
# Bundle selection: OCW_LINUX_BUNDLES=deb ./build_linux.sh (default "deb,appimage"; "rpm" also
# works — Tauri's rpm bundler is pure Rust and needs no rpmbuild).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
# Single source of truth for the version: tauri.conf.json (also stamps the bundle).
VERSION="$(node -p "require('$GUI/src-tauri/tauri.conf.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. x86_64-unknown-linux-gnu
BUNDLES="${OCW_LINUX_BUNDLES:-deb,appimage}"

echo "==> [0/4] checking the Tauri system libraries"
# Fail here with the apt line rather than 400 lines into a cargo build with a pkg-config error.
MISSING=()
for pc in webkit2gtk-4.1 gtk+-3.0; do
  pkg-config --exists "$pc" || MISSING+=("$pc")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "ERROR: missing development libraries: ${MISSING[*]}" >&2
  echo "  Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \\" >&2
  echo "                                  libayatana-appindicator3-dev patchelf file" >&2
  echo "  Fedora:        sudo dnf install webkit2gtk4.1-devel gtk3-devel librsvg2-devel patchelf" >&2
  exit 1
fi
# The AppImage bundler shells out to both; a missing one fails deep inside the bundler.
if [[ ",$BUNDLES," == *",appimage,"* ]]; then
  for tool in patchelf file; do
    command -v "$tool" >/dev/null || {
      echo "ERROR: '$tool' is required for the AppImage bundle (sudo apt install $tool)" >&2
      exit 1
    }
  done
fi

echo "==> [1/4] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/4] staging sidecar resources"
# Onedir bundle (exe + _internal/) ships via Tauri `resources`, landing next to the app binary
# in /usr/lib/OpenWorker/ (.deb) or inside the AppImage's AppDir. rm -rf first: cp WRITES
# THROUGH a symlink at the destination, and this also clears any stale bundle from an earlier
# run whose file set differed.
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar"
# -L (dereference), same as build_dmg.sh: Tauri's resource bundler flattens symlinks into
# duplicate real files anyway, so resolving them here means what we test is what ships.
cp -RL "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/4] tauri build ($BUNDLES)"
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
# NO_STRIP: linuxdeploy strips the binaries it processes by default. A stripped PyInstaller
# executable can no longer locate its embedded archive, which fails as an app that opens
# normally and never brings its server up — so keep this set rather than find out.
( cd "$GUI" && NO_STRIP=true npm run tauri build -- --bundles "$BUNDLES" \
    ${UPDATER_OVERLAY[@]+"${UPDATER_OVERLAY[@]}"} )

echo "==> [4/4] artifacts"
BUNDLE="$GUI/src-tauri/target/release/bundle"
found=0
for f in "$BUNDLE"/deb/*.deb "$BUNDLE"/appimage/*.AppImage "$BUNDLE"/rpm/*.rpm; do
  [ -e "$f" ] || continue
  found=1
  printf '    %s (%s)\n' "$f" "$(du -h "$f" | cut -f1)"
done
[ "$found" = 1 ] || { echo "ERROR: tauri produced no Linux bundles" >&2; exit 1; }

echo ""
echo "Done → OpenWorker $VERSION ($TRIPLE)"
echo "  install the .deb:  sudo apt install $BUNDLE/deb/*.deb"
echo "  run the AppImage:  chmod +x <file>.AppImage && ./<file>.AppImage"
