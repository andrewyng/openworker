#!/usr/bin/env bash
# Set up the built-in browser tools (browser_*) for the OpenWorker macOS app.
#
# The app's bundled Python does not ship Playwright (it would make the download much
# bigger). This script installs Playwright, built for the app's Python 3.12, plus a
# Chromium into OpenWorker's state folder; the app picks both up from there on the next
# browser call — no restart, nothing written inside OpenWorker.app.
#
#   curl -fsSL https://raw.githubusercontent.com/andrewyng/openworker/main/packaging/setup-browser-macos.sh | bash
#   packaging/setup-browser-macos.sh --uninstall
set -euo pipefail

PLAYWRIGHT_VERSION="1.63.0"
# Must match the Python the app is frozen with (.github/workflows/release.yml).
APP_PYTHON="3.12"
DIR="${COWORKER_STATE_DIR:-$HOME/.config/coworker}/browser-runtime"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS only." >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$DIR"
  echo "Removed $DIR"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1 || ! python3 -m pip --version >/dev/null 2>&1; then
  echo "Needs python3 with pip. Install the Xcode command line tools (xcode-select --install) and run this again." >&2
  exit 1
fi

case "$(uname -m)" in
  arm64) PLATFORM="macosx_11_0_arm64" ;;
  x86_64) PLATFORM="macosx_10_13_x86_64" ;;
  *) echo "Unsupported Mac architecture: $(uname -m)" >&2; exit 1 ;;
esac

echo "Installing Playwright $PLAYWRIGHT_VERSION into $DIR ..."
mkdir -p "$DIR"
rm -rf "$DIR/site.tmp"
# Wheels for the APP's Python (cp312), not for the python3 running this script.
python3 -m pip install --quiet --disable-pip-version-check \
  --target "$DIR/site.tmp" \
  --python-version "$APP_PYTHON" --implementation cp --abi "cp${APP_PYTHON/./}" \
  --platform "$PLATFORM" --only-binary=:all: \
  "playwright==$PLAYWRIGHT_VERSION"
rm -rf "$DIR/site"
mv "$DIR/site.tmp" "$DIR/site"

echo "Downloading Chromium ..."
# The Node driver inside the Playwright package installs the browser; no Python 3.12 needed.
DRIVER="$DIR/site/playwright/driver"
PLAYWRIGHT_BROWSERS_PATH="$DIR/browsers" "$DRIVER/node" "$DRIVER/package/cli.js" install chromium

echo "Done. The browser tools work in OpenWorker now."
