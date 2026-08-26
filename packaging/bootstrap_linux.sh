#!/usr/bin/env bash
# One command to take a bare Linux machine to a runnable OpenWorker checkout.
#
#   bash packaging/bootstrap_linux.sh            # ask before installing anything
#   bash packaging/bootstrap_linux.sh --yes      # no prompt
#   bash packaging/bootstrap_linux.sh --packaging # also set up for build_linux.sh
#
# It installs, skipping whatever is already present:
#   1. the system libraries the Tauri shell links against (needs sudo);
#   2. the Rust toolchain, via rustup, if `cargo` isn't on PATH;
#   3. Node 20, via nvm, if `node` is missing or older (Debian 12 ships Node 18);
#   4. the Python venv at .venv (packaging/setup_dev_env.sh);
#   5. the GUI's npm dependencies.
#
# Steps 2 and 3 run the official rustup/nvm installers, which pipe a script from the network
# into a shell and append to your shell profile. That is upstream's supported install path, and
# both are skipped entirely when the tool is already there — but it is your call, which is why
# this asks first. Prefer your distro's own packages? Install Rust and Node yourself, then run
# this: it will detect them and move on.
#
# This is for people BUILDING from source. If you just want to run the app, install the .deb or
# AppImage from a release instead — neither needs any of this. See docs/linux.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
GUI="$ROOT/surfaces/gui"

ASSUME_YES=0
FOR_PACKAGING=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --packaging) FOR_PACKAGING=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

[ "$(uname -s)" = "Linux" ] || {
  echo "This bootstrap is for Linux. macOS/Windows: see packaging/setup_dev_env.sh." >&2
  exit 1
}

# ChromeOS Crostini — worth naming, because two of its quirks bite during a first build.
IS_CROSTINI=0
if [ -e /dev/.cros_milestone ] || [ -d /opt/google/cros-containers ]; then
  IS_CROSTINI=1
fi

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# -- what we need --------------------------------------------------------------------------
# webkit2gtk + gtk are what the shell links against; librsvg renders the icon; patchelf and
# file are what the AppImage bundler shells out to; the rest are the ordinary C toolchain the
# Rust crates and the Python venv expect. python3-venv is separate on Debian/Ubuntu, and
# without it `python3 -m venv` fails at the last step of an otherwise fine setup.
APT_PKGS=(
  build-essential pkg-config curl wget file git
  python3-venv python3-dev
  libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev
  libayatana-appindicator3-dev libssl-dev
  patchelf
)
DNF_PKGS=(
  gcc gcc-c++ make pkgconf-pkg-config curl wget file git
  python3-devel
  webkit2gtk4.1-devel gtk3-devel librsvg2-devel
  libappindicator-gtk3-devel openssl-devel
  patchelf
)

if have apt-get; then
  PM=apt
elif have dnf; then
  PM=dnf
else
  echo "No apt or dnf found. Install these yourself, then re-run:" >&2
  printf '  %s\n' "${APT_PKGS[@]}" >&2
  echo "(names are Debian/Ubuntu's; translate for your distro)" >&2
  exit 1
fi

# -- disk check ----------------------------------------------------------------------------
# A full build is ~7 GB: surfaces/gui/src-tauri/target alone reaches 5.8 GB, plus the cargo
# registry, node_modules, the venv and the frozen sidecar. Running out mid-build wastes the
# whole compile, and Crostini's disk is small by default.
AVAIL_GB="$(df -BG --output=avail "$ROOT" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)"
if [ -n "$AVAIL_GB" ] && [ "$AVAIL_GB" -lt 10 ] 2>/dev/null; then
  echo "WARNING: only ${AVAIL_GB}G free at $ROOT; a full build wants ~10G."
  if [ "$IS_CROSTINI" = 1 ]; then
    echo "  ChromeOS: Settings → Advanced → Developers → Linux → Disk size."
  fi
fi

# -- plan, then consent --------------------------------------------------------------------
say "plan"
echo "  system packages ($PM, needs sudo)"
# Captured BEFORE step 2 runs. Step 2 sources $HOME/.cargo/env into this script so the rest of
# it can use cargo, which makes `have cargo` true afterwards even on a fresh install — and the
# "start a new shell" hint at the end, keyed off that, was suppressed on exactly the case that
# needs it. (Found on a first real run, ChromeOS Crostini, 2026-08-26.) NODE_MAJOR below is
# captured up here for the same reason.
RUST_PREINSTALLED=0
if have cargo; then RUST_PREINSTALLED=1; fi
[ "$RUST_PREINSTALLED" = 1 ] && echo "  Rust:  already installed ($(cargo --version 2>/dev/null))" \
                             || echo "  Rust:  install via rustup (writes to your shell profile)"
NODE_MAJOR=0
if have node; then NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"; fi
[ "$NODE_MAJOR" -ge 20 ] 2>/dev/null && echo "  Node:  already $(node -v)" \
                                     || echo "  Node:  install 20 via nvm (writes to your shell profile)"
echo "  Python venv at $ROOT/.venv"
echo "  npm dependencies in surfaces/gui"
[ "$FOR_PACKAGING" = 1 ] && echo "  plus pyinstaller + typer (for packaging/build_linux.sh)"

if [ "$ASSUME_YES" != 1 ]; then
  # No terminal to ask on (piped, CI, a hook): say so plainly instead of dying on /dev/tty.
  [ -e /dev/tty ] || {
    echo "" >&2
    echo "Not running interactively — re-run with --yes to accept the plan above." >&2
    exit 1
  }
  printf '\nProceed? [y/N] '
  read -r reply </dev/tty
  case "$reply" in [yY]*) ;; *) echo "Nothing was installed."; exit 0 ;; esac
fi

# -- 1. system packages --------------------------------------------------------------------
say "[1/5] system packages"
if [ "$PM" = apt ]; then
  sudo apt-get update
  sudo apt-get install -y "${APT_PKGS[@]}"
else
  sudo dnf install -y "${DNF_PKGS[@]}"
fi

# -- 2. Rust -------------------------------------------------------------------------------
say "[2/5] Rust toolchain"
if [ "$RUST_PREINSTALLED" = 1 ]; then
  echo "    already installed — leaving it alone"
else
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
# Make it usable for the rest of THIS script even on a first install (the profile edit only
# affects shells started later).
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
have cargo || { echo "ERROR: cargo still not on PATH after install" >&2; exit 1; }

# -- 3. Node -------------------------------------------------------------------------------
say "[3/5] Node 20+"
NODE_VIA_NVM=0
if [ "$NODE_MAJOR" -ge 20 ] 2>/dev/null; then
  echo "    already $(node -v) — leaving it alone"
else
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  fi
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm install 20
  nvm use 20
  NODE_VIA_NVM=1
fi
have npm || { echo "ERROR: npm still not on PATH after install" >&2; exit 1; }

# -- 4. Python venv ------------------------------------------------------------------------
say "[4/5] Python venv"
bash "$HERE/setup_dev_env.sh"
if [ "$FOR_PACKAGING" = 1 ]; then
  # Build-time only: pyinstaller freezes the sidecar, and typer is needed because
  # PyInstaller walks mcp.cli, which sys.exit()s at import without it.
  "$ROOT/.venv/bin/pip" install --quiet -e "$ROOT[bedrock]" pyinstaller typer
  echo "    plus pyinstaller + typer"
fi

# -- 5. GUI deps ---------------------------------------------------------------------------
say "[5/5] npm dependencies"
( cd "$GUI" && npm install --no-fund --no-audit )

# -- what now ------------------------------------------------------------------------------
say "ready"
if [ "$RUST_PREINSTALLED" = 0 ] || [ "$NODE_VIA_NVM" = 1 ]; then
  echo "  Start a new shell first (this one predates the PATH changes):"
  echo "      exec \$SHELL -l"
  echo ""
fi
echo "  Run the desktop app from source:"
echo "      cd $GUI && npm run tauri dev"
echo ""
echo "  Or the browser UI (two terminals):"
echo "      $ROOT/.venv/bin/openworker-server --cwd ~/some/project --port 8765"
echo "      cd $GUI && npm run dev"
if [ "$FOR_PACKAGING" = 1 ]; then
  echo ""
  echo "  Build installable packages:"
  echo "      bash packaging/build_linux.sh"
fi
if [ "$IS_CROSTINI" = 1 ]; then
  echo ""
  echo "  ChromeOS: to let OpenWorker reach files outside the container, right-click the"
  echo "  folder in the Files app → 'Share with Linux'. It appears at /mnt/chromeos/MyFiles/."
fi
