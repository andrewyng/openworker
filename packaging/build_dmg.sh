#!/usr/bin/env bash
# Build the macOS desktop app + a drag-to-install .dmg.
#
#   1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
#   2. Stage it at binaries/sidecar/ for Tauri's `resources` slot (+ sign its Mach-Os).
#   3. `tauri build --bundles app` → OpenWorker.app (resources are copied in).
#   4. Wrap the .app in a compressed .dmg via hdiutil (reliable + headless; Tauri's own
#      bundle_dmg.sh uses Finder AppleScript and fails in non-interactive sessions).
#
# Prerequisites (mirrors build_windows.ps1's header):
#   - Rust (rustup) + Node/npm, and the GUI deps installed (npm ci in surfaces/gui).
#   - A Python venv at .venv (repo root) with this package installed editable, plus the
#     build-only deps:
#       python3 -m venv .venv
#       .venv/bin/pip install -e '.[bedrock,browser]' pyinstaller tzdata typer
#     `typer` is needed only at BUILD time: PyInstaller walks the `mcp` package and
#     `mcp.cli` calls sys.exit() at import if typer is absent, which aborts the freeze.
#     (aisuite installs like any other dependency — git-pinned in pyproject.toml.)
#
# SIGNING: set APPLE_SIGNING_IDENTITY to a "Developer ID Application: … (TEAMID)" identity and
# `tauri build` signs the .app + the bundled sidecar with it. Left unset → UNSIGNED (first launch
# needs right-click → Open).
#
# NOTARIZATION (step 5, runs only when the identity is set): signs the .dmg CONTAINER, submits
# to Apple's notary service, staples the ticket, and verifies with spctl. Signing alone is NOT
# enough for public downloads — un-notarized apps get macOS's "Apple could not verify… Move to
# Trash?" dialog. Auth is an App Store Connect API key via NOTARYTOOL_API_KEY_PATH /
# NOTARYTOOL_API_KEY_ID / NOTARYTOOL_API_ISSUER_ID — exported, or in $OCW_NOTARY_ENV, or in
# `.ocw-notary.env` one directory ABOVE the repo (shared by every clone/worktree on a machine,
# never committed). Vars missing → the DMG is still produced, with a loud warning.
#
# LOCAL ITERATION: leave APPLE_SIGNING_IDENTITY unset for a fully unsigned dev build, or set
# OCW_SKIP_NOTARIZE=1 to sign but skip the slow notary round-trip. Neither is distributable.
#
# Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
# the spec strips coworker.connectors.experimental. Self-builders can opt in with:
#   COWORKER_EXPERIMENTAL=1 ./build_dmg.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
APP="OpenWorker"
# Single source of truth for the version: tauri.conf.json (also stamps the bundle).
VERSION="$(node -p "require('$GUI/src-tauri/tauri.conf.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. aarch64-apple-darwin
ARCH="${TRIPLE%%-*}"

# CI keychain bootstrap: on a fresh runner the Developer ID cert exists only as the
# APPLE_CERTIFICATE secret (base64 .p12) — import it into a throwaway keychain so the
# sidecar codesign calls below can find the identity ("no identity found", v0.1.3 run
# 29773913622). tauri build does its OWN import later; this covers our signing, which
# runs first. Local builds never set APPLE_CERTIFICATE — the identity already lives in
# the login keychain, and this block is skipped.
if [ -n "${APPLE_CERTIFICATE:-}" ] && [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> importing signing certificate into a temporary keychain"
  KC_DIR="$(mktemp -d)"
  KC="$KC_DIR/ocw-signing.keychain-db"
  KC_PASS="$(openssl rand -hex 16)"
  security create-keychain -p "$KC_PASS" "$KC"
  security set-keychain-settings -lut 21600 "$KC"
  security unlock-keychain -p "$KC_PASS" "$KC"
  echo "$APPLE_CERTIFICATE" | base64 -d > "$KC_DIR/cert.p12"
  security import "$KC_DIR/cert.p12" -P "${APPLE_CERTIFICATE_PASSWORD:-}" \
    -A -t cert -f pkcs12 -k "$KC"
  rm -f "$KC_DIR/cert.p12"
  # Allow codesign to use the key headlessly (no UI prompt exists on a runner).
  security set-key-partition-list -S "apple-tool:,apple:" -s -k "$KC_PASS" "$KC" >/dev/null
  security list-keychains -d user -s "$KC" login.keychain-db
fi

# Install Chromium inside the Playwright package before freezing. This is Playwright's
# supported PyInstaller layout: collect_all("playwright") below carries exactly the browser
# revision paired with the pinned Python package, so an installed OpenWorker never downloads
# an executable on first use.
echo "==> [1/6] Playwright: staging bundled Chromium"
PLAYWRIGHT_BROWSERS_PATH=0 "$PLATFORM/.venv/bin/python" -m playwright install chromium --no-shell

echo "==> [2/6] PyInstaller: bundling openworker-server ($TRIPLE)"
"$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/openworker-server.spec"

echo "==> [3/6] staging sidecar + Chrome native-host resources"
# Onedir bundle (exe + _internal/) ships via Tauri `resources` as Contents/Resources/sidecar/
# — onefile's per-launch self-extraction cost 6-7s of boot splash. rm -rf first: cp WRITES
# THROUGH a symlink at the destination (a dev-convenience symlink in the old externalBin slot
# once clobbered another worktree's venv console script, caught 2026-07-11); also clears any
# stale onefile binary from pre-onedir builds.
mkdir -p "$GUI/src-tauri/binaries"
rm -rf "$GUI/src-tauri/binaries/sidecar" "$GUI/src-tauri/binaries/openworker-server-$TRIPLE"
# Chromium is excluded from PyInstaller and injected intact after Tauri copies
# resources. Dereference the ordinary onedir symlinks here (notably Python.framework)
# so Tauri receives a self-contained, symlink-free sidecar tree.
cp -RL "$HERE/dist/openworker-server" "$GUI/src-tauri/binaries/sidecar"
if [ -n "$(find "$GUI/src-tauri/binaries/sidecar" -type l | head -1)" ]; then
  echo "ERROR: a symlink survived sidecar staging" >&2
  exit 1
fi
# Drop the pseudo-framework: after dereferencing, Python.framework is just a duplicate of
# _internal/Python (which the PyInstaller bootloader actually loads — verified by running
# the sidecar without it) plus an Info.plist. Any file living under a *.framework/ path
# triggers codesign/notary bundle inference, which can NEVER validate this flattened
# layout — three Invalid notarization verdicts (f73463f3, ca30027a, + one more) before
# this removal. No .framework may ever ship inside the sidecar resources.
rm -rf "$GUI/src-tauri/binaries/sidecar/_internal/Python.framework"
if [ -n "$(find "$GUI/src-tauri/binaries/sidecar" -type d -name "*.framework" \
  ! -path "*/playwright/driver/package/.local-browsers/*" | head -1)" ]; then
  echo "ERROR: a non-browser .framework appeared in the sidecar" >&2
  exit 1
fi
chmod +x "$GUI/src-tauri/binaries/sidecar/openworker-server"

# Chrome launches this small exact-origin process through Native Messaging. It is
# deliberately separate from the Python sidecar: neither the random localhost port
# nor either authentication token is exposed to the extension.
cargo build --release --locked --manifest-path "$PLATFORM/browser-native-host/Cargo.toml"
NATIVE_HOST_DIR="$GUI/src-tauri/binaries/native-host"
rm -rf "$NATIVE_HOST_DIR"
mkdir -p "$NATIVE_HOST_DIR"
cp "$PLATFORM/browser-native-host/target/release/openworker-browser-native-host" \
  "$NATIVE_HOST_DIR/openworker-browser-native-host"
chmod +x "$NATIVE_HOST_DIR/openworker-browser-native-host"
cp "$PLATFORM/browser-native-host/com.openworker.browser.json.template" "$NATIVE_HOST_DIR/"

# Sign the sidecar's Mach-O files BEFORE tauri build: `tauri build` signs the .app (sealing
# resources into its signature) but does NOT sign nested binaries inside resources — unsigned
# Mach-Os there fail notarization. Hardened runtime + timestamp on every one, same identity,
# entitlements on the executable (disable-library-validation: the bundled python dylibs carry
# other Team IDs). externalBin used to get this from tauri itself.
if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "    signing sidecar binaries"
  SIDECAR="$GUI/src-tauri/binaries/sidecar"
  BROWSER_REL="playwright/driver/package/.local-browsers"
  # Every Mach-O gets a plain FILE signature (no framework-bundle signing: the staged
  # tree is fully dereferenced, so each file must validate standalone — that is exactly
  # what the notary service checks). Chromium is excluded here and signed as nested
  # bundles after Tauri build, once its symlinks have been restored. Entitlements only on the entrypoint
  # (disable-library-validation: the bundled python.org dylibs carry another Team ID).
  find "$SIDECAR" -type f ! -name "openworker-server" \
    ! -path "*/$BROWSER_REL/*" \
    ! -name "*.py" ! -name "*.pyc" ! -name "*.txt" ! -name "*.pem" ! -name "*.json" \
    -print0 | while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" || continue
    codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime "$f"
  done
  codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime \
    --entitlements "$GUI/src-tauri/entitlements.plist" "$SIDECAR/openworker-server"
  codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime \
    "$NATIVE_HOST_DIR/openworker-browser-native-host"
fi

echo "==> [4/6] tauri build (.app)"
# Auto-update artifacts (.app.tar.gz + minisign .sig) are produced only when the updater
# signing key is available — from the env (CI secret TAURI_SIGNING_PRIVATE_KEY), or from
# `.ocw-updater.env` one directory above the repo (same convention as the notary env).
# They must be created AFTER Chromium is injected below: asking Tauri to create them during
# this build would sign a pre-injection app that is not the app shipped in the DMG.
UPDATER_ENV="${OCW_UPDATER_ENV:-$PLATFORM/../.ocw-updater.env}"
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ] && [ -f "$UPDATER_ENV" ]; then
  # shellcheck disable=SC1090
  source "$UPDATER_ENV"
fi
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  echo "    WARNING: no updater signing key — building WITHOUT auto-update artifacts (not releasable)."
fi

BUNDLE="$GUI/src-tauri/target/release/bundle"
UPDATER_NAME="$APP.app.tar.gz"
UPDATER_ARCHIVE="$BUNDLE/macos/$UPDATER_NAME"
UPDATER_SIGNATURE="$UPDATER_ARCHIVE.sig"
UPDATER_PROVENANCE="$UPDATER_ARCHIVE.final.sha256"
cleanup_updater_artifacts() {
  rm -f "$UPDATER_ARCHIVE" "$UPDATER_SIGNATURE" "$UPDATER_PROVENANCE"
}
# A keyless build must not inherit updater output from an earlier signed local build.
cleanup_updater_artifacts
( cd "$GUI" && npm run tauri build -- --bundles app )

# PyInstaller correctly discovers Playwright's browser, but Tauri's resource copier
# dereferences macOS framework symlinks. Restore the exact installed browser tree in the
# finished app before its final signature and the DMG are produced.
BROWSER_SOURCE="$("$PLATFORM/.venv/bin/python" -c \
  'from pathlib import Path; import playwright; print(Path(playwright.__file__).resolve().parent / "driver/package/.local-browsers")')"
BROWSER_DEST="$BUNDLE/macos/$APP.app/Contents/Resources/sidecar/_internal/playwright/driver/package/.local-browsers"
case "$BROWSER_SOURCE" in
  "$PLATFORM"/.venv/*/playwright/driver/package/.local-browsers) ;;
  *)
    echo "ERROR: refusing unexpected Playwright browser source: $BROWSER_SOURCE" >&2
    exit 1
    ;;
esac
if [ ! -d "$BROWSER_SOURCE" ] || [ ! -d "$(dirname "$BROWSER_DEST")" ]; then
  echo "ERROR: unable to locate the staged Playwright browser tree" >&2
  exit 1
fi
rm -rf "$BROWSER_DEST"
/usr/bin/ditto "$BROWSER_SOURCE" "$BROWSER_DEST"
if [ -z "$(find "$BROWSER_DEST" -type l | head -1)" ]; then
  echo "ERROR: Chromium framework symlinks were not preserved" >&2
  exit 1
fi

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "    signing nested Chromium bundles"
  # Sign raw Mach-O code first, then bundle directories depth-first (helper apps,
  # framework, outer Chrome app), and finally reseal OpenWorker after replacing the
  # resource subtree.
  find "$BROWSER_DEST" -type f -print0 | while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" || continue
    codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime "$f"
  done
  find "$BROWSER_DEST" -depth -type d \
    \( -name "*.xpc" -o -name "*.app" -o -name "*.framework" \) -print0 \
    | while IFS= read -r -d '' bundle; do
        case "$(basename "$bundle")" in
          *"Helper (Renderer).app"|*"Helper (GPU).app")
            codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp \
              --options runtime \
              --entitlements "$HERE/chromium-helper-entitlements.plist" \
              "$bundle"
            ;;
          *)
            codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp \
              --options runtime "$bundle"
            ;;
        esac
      done
  codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime \
    --entitlements "$GUI/src-tauri/entitlements.plist" \
    "$BUNDLE/macos/$APP.app"
  codesign --verify --deep --strict --verbose=2 "$BUNDLE/macos/$APP.app"
fi

# Tauri's macOS updater is a gzip-compressed tar with the .app as its single root.
# Recreate that format from the final, Chromium-bearing app, validate the archive shape
# (including a preserved Chromium framework symlink), then sign those exact bytes.
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  echo "    creating updater artifacts from the final app"
  COPYFILE_DISABLE=1 /usr/bin/tar -czf "$UPDATER_ARCHIVE" \
    -C "$BUNDLE/macos" "$APP.app" \
    || { cleanup_updater_artifacts; exit 1; }
  "$PLATFORM/.venv/bin/python" - "$UPDATER_ARCHIVE" "$APP.app" <<'PY' \
    || { cleanup_updater_artifacts; exit 1; }
import sys
import tarfile

archive_path, app_name = sys.argv[1:]
root = f"{app_name}/"
browser_segment = "/playwright/driver/package/.local-browsers/"
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()

unexpected = [
    member.name
    for member in members
    if member.name != app_name and not member.name.startswith(root)
]
if not members or unexpected:
    raise SystemExit(
        f"invalid updater archive root (unexpected entries: {unexpected[:3]})"
    )
if not any(browser_segment in f"/{member.name}/" for member in members):
    raise SystemExit("updater archive is missing the bundled Chromium tree")
if not any(
    member.issym() and browser_segment in f"/{member.name}/"
    for member in members
):
    raise SystemExit("updater archive flattened Chromium framework symlinks")
PY
  ( cd "$GUI" && npm run tauri -- signer sign "$UPDATER_ARCHIVE" ) \
    || { cleanup_updater_artifacts; exit 1; }
  if [ ! -s "$UPDATER_ARCHIVE" ] || [ ! -s "$UPDATER_SIGNATURE" ]; then
    echo "ERROR: updater archive/signature pair is incomplete" >&2
    cleanup_updater_artifacts
    exit 1
  fi
  # release.yml requires this checksum receipt before staging either file. Tauri does
  # not create it, so a pre-injection or half-regenerated artifact fails closed.
  ( cd "$BUNDLE/macos" \
    && shasum -a 256 "$UPDATER_NAME" "$UPDATER_NAME.sig" \
      > "$UPDATER_NAME.final.sha256" ) \
    || { cleanup_updater_artifacts; exit 1; }
fi

echo "==> [5/6] hdiutil: wrapping into .dmg"
STAGING="$(mktemp -d)"
cp -R "$BUNDLE/macos/$APP.app" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
# Background art (arrow + "drag to Applications") — hidden folder Finder reads for the window.
# A HiDPI TIFF (1x + native 2x reps) so text/arrow stay crisp on Retina; a plain 1x PNG would
# be upscaled and look hazy/pixelated.
mkdir "$STAGING/.background"
cp "$HERE/dmg-background.tiff" "$STAGING/.background/bg.tiff"
DMG="$BUNDLE/dmg/${APP}_${VERSION}_${ARCH}.dmg"
mkdir -p "$(dirname "$DMG")"
rm -f "$DMG"

# A styled install window (fixed size, icons in place, arrow background) instead of Finder's
# default oversized bare window. Needs Finder (AppleScript); if it isn't available (headless CI),
# fall back to the plain compressed image so the build still produces a working .dmg.
#
# Two hard-won correctness points (both caused a *silently* unstyled .dmg before):
#   1. A stale "$APP" volume already mounted → our RW image mounts as "$APP 1", and a hardcoded
#      `tell disk "$APP"` then styles the WRONG (stale) volume, so our image never gets a
#      .DS_Store. Detach any pre-existing mount first, and target the ACTUAL mounted name.
#   2. Finder writes .DS_Store asynchronously — detaching too soon drops it. Poll until it lands.
style_dmg() {
  # Clear any earlier mount of this volume so we don't collide into "$APP 1".
  [ -d "/Volumes/$APP" ] && hdiutil detach "/Volumes/$APP" -force >/dev/null 2>&1 || true
  local rw; rw="$(mktemp -u).dmg"
  hdiutil create -volname "$APP" -srcfolder "$STAGING" -fs HFS+ -format UDRW -ov "$rw" >/dev/null
  local info dev mnt vol
  info="$(hdiutil attach -readwrite -noverify -noautoopen "$rw")"
  dev="$(echo "$info" | grep -Eo '^/dev/disk[0-9]+' | head -1)"
  mnt="$(echo "$info" | grep -Eo '/Volumes/.*$' | head -1)"
  [ -n "$dev" ] && [ -n "$mnt" ] || return 1
  vol="$(basename "$mnt")"   # the real mounted name — what `tell disk` must target
  sleep 1
  # Icons at y≈190 to sit on the background's arrow: app left of it, Applications right. Background
  # via the relative HFS path (`file ".background:bg.tiff"`) so the alias survives a rename; the
  # close→open→update dance forces Finder to actually write the .DS_Store.
  osascript <<OSA || { hdiutil detach "$dev" -force >/dev/null 2>&1 || true; return 1; }
tell application "Finder"
  tell disk "$vol"
    open
    delay 1
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 840, 543}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 96
    set text size of opts to 12
    set background picture of opts to file ".background:bg.tiff"
    set position of item "$APP.app" of container window to {172, 190}
    set position of item "Applications" of container window to {468, 190}
    close
    open
    update without registering applications
    delay 3
  end tell
end tell
OSA
  # Wait for Finder to flush .DS_Store into the image (else the layout is lost).
  local i; for i in $(seq 1 15); do [ -f "$mnt/.DS_Store" ] && break; sleep 1; done
  [ -f "$mnt/.DS_Store" ] || { hdiutil detach "$dev" -force >/dev/null 2>&1 || true; return 1; }
  sync; sync
  hdiutil detach "$dev" -force >/dev/null
  hdiutil convert "$rw" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
  rm -f "$rw"
}

if ! style_dmg; then
  echo "    (Finder styling unavailable — writing a plain .dmg)"
  hdiutil create -volname "$APP" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
fi
rm -rf "$STAGING"

if [ "${OCW_SKIP_NOTARIZE:-}" = "1" ] && [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  # Local-iteration escape hatch: sign (seconds) but skip the notary round-trip
  # (minutes). Locally built DMGs carry no quarantine flag, so Gatekeeper never
  # prompts on this machine anyway. NEVER distribute a build made this way.
  echo "==> [6/6] OCW_SKIP_NOTARIZE=1 — signing container, SKIPPING notarize/staple (do not distribute)"
  codesign --sign "$APPLE_SIGNING_IDENTITY" --timestamp "$DMG"
elif [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> [6/6] release finishing: sign container → notarize → staple"
  codesign --sign "$APPLE_SIGNING_IDENTITY" --timestamp "$DMG"

  # CI provides the App Store Connect key under tauri's APPLE_API_* names (release.yml)
  # — reuse the same key for the DMG-container notarization below.
  NOTARYTOOL_API_KEY_PATH="${NOTARYTOOL_API_KEY_PATH:-${APPLE_API_KEY_PATH:-}}"
  NOTARYTOOL_API_KEY_ID="${NOTARYTOOL_API_KEY_ID:-${APPLE_API_KEY:-}}"
  NOTARYTOOL_API_ISSUER_ID="${NOTARYTOOL_API_ISSUER_ID:-${APPLE_API_ISSUER:-}}"

  NOTARY_ENV="${OCW_NOTARY_ENV:-$PLATFORM/../.ocw-notary.env}"
  if [ -z "${NOTARYTOOL_API_KEY_PATH:-}" ] && [ -f "$NOTARY_ENV" ]; then
    set -a; # shellcheck disable=SC1090
    source "$NOTARY_ENV"; set +a
  fi
  if [ -n "${NOTARYTOOL_API_KEY_PATH:-}" ] && [ -n "${NOTARYTOOL_API_KEY_ID:-}" ] \
     && [ -n "${NOTARYTOOL_API_ISSUER_ID:-}" ]; then
    xcrun notarytool submit "$DMG" \
      --key "$NOTARYTOOL_API_KEY_PATH" \
      --key-id "$NOTARYTOOL_API_KEY_ID" \
      --issuer "$NOTARYTOOL_API_ISSUER_ID" \
      --wait
    xcrun stapler staple "$DMG"
    # The same check Gatekeeper runs on download — fail the build rather than ship a
    # DMG that greets users with the "Move to Trash" malware dialog.
    spctl -a -t open --context context:primary-signature "$DMG"
    echo "    Gatekeeper: accepted (notarized + stapled)"
  else
    echo "    WARNING: DMG is signed but NOT notarized — public downloads will see the"
    echo "    'Move to Trash' dialog. Provide NOTARYTOOL_API_KEY_PATH/_KEY_ID/_ISSUER_ID"
    echo "    (env, \$OCW_NOTARY_ENV, or $NOTARY_ENV)."
  fi
else
  echo "    (unsigned dev build — set APPLE_SIGNING_IDENTITY for a distributable DMG)"
fi

echo ""
echo "Done → $DMG"
