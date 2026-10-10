#!/usr/bin/env bash
# Build the macOS `openworker` program: one folder with the program and its support files
# (no Python needed on the Mac), packed as openworker-<version>-macos-<arch>.tar.gz.
#
#   1. PyInstaller with packaging/openworker-server.spec, OPENWORKER_BUNDLE=cli: the whole
#      command line as the entry point (the same freeze as the Linux program).
#   2. With APPLE_SIGNING_IDENTITY set: sign every Mach-O file (hardened runtime), submit
#      the folder to Apple's notary service, and check it the way Gatekeeper does. The
#      ticket for a command-line program lives with Apple, not in the files, so there is
#      nothing to staple. Without the identity the program is unsigned: fine for your own
#      Mac, not for distribution.
#   3. Pack the folder and write its SHA-256 next to it, in packaging/dist/.
#
# Prerequisites: the .venv that build_dmg.sh uses (the package with the bedrock and
# openshell extras, pyinstaller, typer), Xcode command line tools. Notary credentials as
# for build_dmg.sh: NOTARYTOOL_API_KEY_PATH / _KEY_ID / _ISSUER_ID, the APPLE_API_* names
# from CI, or `.ocw-notary.env` one directory above the repo. OCW_SKIP_NOTARIZE=1 signs
# but does not notarize.
#
# Install on a Mac (packaging/install.sh does this):
#   tar -xzf openworker-<version>-macos-<arch>.tar.gz -C ~/.local/share
#   ln -sf ~/.local/share/openworker/openworker ~/.local/bin/openworker
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="$HERE/dist"
VENV="$ROOT/.venv"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
ARCH="$(uname -m)"   # arm64 or x86_64
NAME="openworker-$VERSION-macos-$ARCH"
PROGRAM="$OUT/openworker"

[ "$(uname -s)" = "Darwin" ] || { echo "ERROR: this script builds on macOS only (Linux: build_linux.sh)" >&2; exit 1; }
[ -x "$VENV/bin/pyinstaller" ] || { echo "ERROR: $VENV has no pyinstaller; see the VENV PREREQS in build_dmg.sh" >&2; exit 1; }
"$VENV/bin/python" -c "import grpc" 2>/dev/null || { echo "ERROR: grpcio is missing from .venv; install the openshell extra" >&2; exit 1; }

mkdir -p "$OUT"
rm -rf "$PROGRAM" "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256" "$OUT/$NAME.zip"

echo "==> [1/3] PyInstaller: $NAME"
OPENWORKER_BUNDLE=cli "$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$OUT" --workpath "$HERE/build" "$HERE/openworker-server.spec"
# Resolve every link into a real file first. With a framework Python (python.org, Homebrew,
# the CI runner's) PyInstaller's _internal/Python is a link INTO Python.framework, and the
# framework must then go: codesign and the notary service treat it as a bundle, which this
# flattened layout can never satisfy (the sidecar learned this; see build_dmg.sh). Once the
# links are resolved the framework is only a duplicate of _internal/Python.
cp -RL "$PROGRAM" "$PROGRAM.flat"
rm -rf "$PROGRAM"
mv "$PROGRAM.flat" "$PROGRAM"
rm -rf "$PROGRAM/_internal/Python.framework"
if [ -n "$(find "$PROGRAM" -type d -name "*.framework" | head -1)" ]; then
  echo "ERROR: a .framework is in the program folder; it cannot pass notarization" >&2
  exit 1
fi
chmod +x "$PROGRAM/openworker"
# The program must start before anything is signed or submitted.
"$PROGRAM/openworker" version

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> [2/3] signing"
  # Every Mach-O gets its own file signature with the hardened runtime; the entry point
  # also gets the entitlements (disable-library-validation: the bundled Python libraries
  # carry python.org's Team ID).
  find "$PROGRAM" -type f ! -name "openworker" \
    ! -name "*.py" ! -name "*.pyc" ! -name "*.txt" ! -name "*.pem" ! -name "*.json" \
    -print0 | while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" || continue
    codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime "$f"
  done
  codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime \
    --entitlements "$ROOT/surfaces/gui/src-tauri/entitlements.plist" "$PROGRAM/openworker"

  if [ "${OCW_SKIP_NOTARIZE:-}" = "1" ]; then
    echo "    OCW_SKIP_NOTARIZE=1: signed, NOT notarized (do not distribute)"
  else
    NOTARYTOOL_API_KEY_PATH="${NOTARYTOOL_API_KEY_PATH:-${APPLE_API_KEY_PATH:-}}"
    NOTARYTOOL_API_KEY_ID="${NOTARYTOOL_API_KEY_ID:-${APPLE_API_KEY:-}}"
    NOTARYTOOL_API_ISSUER_ID="${NOTARYTOOL_API_ISSUER_ID:-${APPLE_API_ISSUER:-}}"
    NOTARY_ENV="${OCW_NOTARY_ENV:-$ROOT/../.ocw-notary.env}"
    if [ -z "${NOTARYTOOL_API_KEY_PATH:-}" ] && [ -f "$NOTARY_ENV" ]; then
      set -a; # shellcheck disable=SC1090
      source "$NOTARY_ENV"; set +a
    fi
    if [ -n "${NOTARYTOOL_API_KEY_PATH:-}" ] && [ -n "${NOTARYTOOL_API_KEY_ID:-}" ] \
       && [ -n "${NOTARYTOOL_API_ISSUER_ID:-}" ]; then
      echo "    notarizing"
      # The notary service takes a zip of the folder. The ticket is recorded with Apple;
      # the files themselves do not change, so the tarball packed below is what was checked.
      ditto -c -k --keepParent "$PROGRAM" "$OUT/$NAME.zip"
      xcrun notarytool submit "$OUT/$NAME.zip" \
        --key "$NOTARYTOOL_API_KEY_PATH" \
        --key-id "$NOTARYTOOL_API_KEY_ID" \
        --issuer "$NOTARYTOOL_API_ISSUER_ID" \
        --wait
      rm -f "$OUT/$NAME.zip"
      # `notarytool submit --wait` exits non-zero when Apple does not accept. spctl cannot
      # assess a bare command-line program ("does not seem to be an app"), so the local
      # check is the signature itself.
      codesign --verify --strict --deep -v "$PROGRAM/openworker"
      echo "    notarized and accepted"
    else
      echo "    WARNING: signed but NOT notarized. Provide NOTARYTOOL_API_KEY_PATH/_KEY_ID/_ISSUER_ID"
      echo "    (env, \$OCW_NOTARY_ENV, or $NOTARY_ENV)."
    fi
  fi
else
  echo "==> [2/3] unsigned (set APPLE_SIGNING_IDENTITY for a distributable program)"
fi

echo "==> [3/3] packing"
"$PROGRAM/openworker" version
tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" openworker
(cd "$OUT" && shasum -a 256 "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
ls -la "$OUT/$NAME.tar.gz"
cat "$OUT/$NAME.tar.gz.sha256"
