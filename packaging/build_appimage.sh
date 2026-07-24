#!/usr/bin/env bash
# Build the Linux desktop app as an AppImage.
#
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot.
#   3. `tauri build --bundles appimage` → OpenWorker_<version>_<arch>.AppImage
#      (+ updater artifacts OpenWorker_<version>_<arch>.AppImage.tar.gz + .sig when the
#      updater signing key is available).
#
# Why AppImage: OpenWorker is a host-acting agent (shell, files, MCP subprocesses), so a
# sandboxed format (Flatpak/Snap) would fight the app's nature. AppImage ships one portable
# file with full host access — and it is the ONLY Linux bundle the Tauri updater supports,
# so picking it keeps auto-update working (deb/rpm throw "Cannot run updater on this Linux
# package" at install time).
#
# Prerequisites (mirrors build_dmg.sh's header):
#   - Rust (rustup) + Node/npm, and the GUI deps installed (npm ci in surfaces/gui).
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3.12 -m venv .venv
#       .venv/bin/pip install -e . pyinstaller typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and
#     `mcp.cli` calls sys.exit() at import if typer is absent, which aborts the freeze.
#     (aisuite installs like any other dependency — git-pinned in pyproject.toml.)
#   - Tauri's Linux build-time libs. Fedora:
#       dnf install webkit2gtk4.1-devel gtk3-devel libappindicator-gtk3-devel \
#                   librsvg2-devel libsoup3-devel patchelf alsa-lib-devel
#     Debian/Ubuntu:
#       apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev \
#                   librsvg2-dev libsoup-3.0-dev patchelf libasound2-dev
#     `alsa-lib-devel` / `libasound2-dev` is required by cpal (the STT sidecar's audio
#     backend) — without alsa.pc the `ocw-stt` crate fails to compile.
#     Tauri downloads appimagetool + linuxdeploy itself on first build.
#     (At runtime an AppImage needs webkit2gtk-4.1, gtk3, libsoup3-3.0, libasound2 on the
#     host — standard on any modern distro.)
#
# AUTO-UPDATE (optional): set TAURI_SIGNING_PRIVATE_KEY to produce the .AppImage.tar.gz +
# .sig artifacts the updater manifest (packaging/make_update_manifest.py) references.
# From the env, or from `.ocw-updater.env` one directory above the repo (same convention
# as build_dmg.sh). Keyless builds skip the overlay entirely so dev/fork builds still work;
# a keyless RELEASE would strand every install without auto-update, hence the loud warning.
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
# the spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_appimage.sh
set -euo pipefail

# NO_STRIP=1: linuxdeploy bundles its own `strip` (an old GNU binutils inside the
# AppImage) that predates the `.relr.dyn` ELF section (type 0x13, SHT_RELR, added in
# binutils 2.31). Libraries built on modern distros (Fedora 40+, recent Ubuntu) carry
# that section, so the bundled strip rejects EVERY .so ("unknown type [0x13] section
# `.relr.dyn'") and linuxdeploy aborts. NO_STRIP tells linuxdeploy-plugin-appimage to
# skip the strip pass entirely — the libraries are already release builds, so the only
# cost is keeping their (typically already-stripped) debug sections. Harmless on older
# distros too, so set unconditionally.
export NO_STRIP=1

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
APP="OpenWorker"
# Single source of truth for the version: tauri.conf.json (also stamps the bundle).
VERSION="$(node -p "require('$GUI/src-tauri/tauri.conf.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. x86_64-unknown-linux-gnu
ARCH="${TRIPLE%%-*}"                            # x86_64 | aarch64
# AppImage bundle naming (and Tauri's --target) uses the Debian arch convention,
# not the GNU triple: x86_64 -> amd64, aarch64 -> arm64.
case "$ARCH" in
  x86_64) BUNDLE_ARCH="amd64";;
  aarch64) BUNDLE_ARCH="arm64";;
  *) BUNDLE_ARCH="$ARCH";;
esac

echo "==> [1/3] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [2/3] staging sidecar resources"
# Onedir bundle (exe + _internal/) ships via Tauri `resources` as usr/lib/<binary>/sidecar/
# inside the AppImage (see server_bin()'s Linux candidate in src-tauri/src/lib.rs). rm -rf
# first: cp writes through a symlink at the destination; also clears any stale onefile binary
# from pre-onedir builds.
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
# -RL (dereference): Tauri's resource bundler flattens symlinks into duplicate REAL files.
# Dereferencing at staging makes what tauri COPIES deterministic; no framework layout is
# produced on Linux (unlike macOS), but dereferencing keeps the two build scripts identical
# and avoids any stray symlink surviving into the AppDir.
cp -RL "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
if [ -n "$(find "$GUI/src-tauri/binaries/sidecar" -type l | head -1)" ]; then
  echo "ERROR: symlinks survived sidecar staging — tauri would flatten them into duplicates" >&2
  exit 1
fi
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

echo "==> [3/3] tauri build (AppImage)"
# Auto-update artifacts (.AppImage.tar.gz + minisign .sig): produced only when the updater
# signing key is available — from the env (CI secret TAURI_SIGNING_PRIVATE_KEY), or from
# `.ocw-updater.env` one directory above the repo (same convention as the notary env on macOS).
# Keyless builds skip the overlay entirely so dev/fork builds keep working; keyless RELEASES
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
# ${arr[@]+…} guard: plain "${arr[@]}" on an EMPTY array is an "unbound variable" under set -u.
# NO_STRIP=1 is exported at the top of this script (see comment there).
( cd "$GUI" && npm run tauri build -- --bundles appimage ${UPDATER_OVERLAY[@]+"${UPDATER_OVERLAY[@]}"} )

BUNDLE="$GUI/src-tauri/target/release/bundle/appimage"
APPIMAGE="$BUNDLE/${APP}_${VERSION}_${BUNDLE_ARCH}.AppImage"

if [ ! -f "$APPIMAGE" ]; then
  echo "ERROR: expected AppImage not found at $APPIMAGE" >&2
  echo "       (Tauri may have named it differently — check $BUNDLE/)" >&2
  exit 1
fi

echo ""
echo "Done → $APPIMAGE"
echo "  run:      chmod +x \"$APPIMAGE\" && \"$APPIMAGE\""
echo "  update:   (set TAURI_SIGNING_PRIVATE_KEY to also emit the .tar.gz + .sig for the updater manifest)"
