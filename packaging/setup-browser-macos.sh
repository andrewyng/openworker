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

# Playwright 1.63's Node driver needs macOS 13.5 and its Chromium macOS 14. Only the
# browser tools need this; the rest of OpenWorker runs on macOS 12+.
if [ "$(sw_vers -productVersion | cut -d. -f1)" -lt 14 ]; then
  echo "The browser tools need macOS 14 or later (this Mac runs $(sw_vers -productVersion))." >&2
  echo "The rest of OpenWorker works without them." >&2
  exit 1
fi

# The wheels must match the Python the app is frozen with, so read it from the installed
# app rather than hard-coding it (the script is fetched from main, the app may be older).
APP_PYTHON="3.12"
for found in "${OPENWORKER_APP:-/Applications/OpenWorker.app}"/Contents/Resources/sidecar/_internal/python3.* \
             "$HOME"/Applications/OpenWorker.app/Contents/Resources/sidecar/_internal/python3.*; do
  if [ -e "$found" ]; then
    APP_PYTHON="${found##*/python}"
    break
  fi
done

if ! command -v python3 >/dev/null 2>&1 || ! python3 -m pip --version >/dev/null 2>&1; then
  echo "Needs python3 with pip. Install the Xcode command line tools (xcode-select --install) and run this again." >&2
  exit 1
fi

case "$(uname -m)" in
  # 12.0 is the app's minimum; pip also accepts wheels built for older versions
  # (greenlet's cp312 wheel is macosx_11_0_universal2, which 10_13 would reject).
  arm64) PLATFORM="macosx_12_0_arm64" ;;
  x86_64) PLATFORM="macosx_12_0_x86_64" ;;
  *) echo "Unsupported Mac architecture: $(uname -m)" >&2; exit 1 ;;
esac

UPGRADE=""
[ -d "$DIR/site" ] && UPGRADE=1

echo "Installing Playwright $PLAYWRIGHT_VERSION for Python $APP_PYTHON into $DIR ..."
mkdir -p "$DIR"
rm -rf "$DIR/site.tmp"
# Wheels for the APP's Python, not for the python3 running this script.
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

if [ -n "$UPGRADE" ]; then
  # A running app keeps the old Playwright loaded; mixing it with the new driver fails.
  echo "Done. Quit and reopen OpenWorker to use the updated browser."
else
  echo "Done. The browser tools work in OpenWorker now."
fi
