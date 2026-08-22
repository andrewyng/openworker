#!/usr/bin/env bash
# Install the build-time system dependencies for OpenWorker on Linux.
#
# Two groups are needed to build the desktop app (packaging/build_linux.sh):
#   1. Tauri 2 shell + webkit GUI  — libwebkit2gtk-4.1, appindicator, librsvg, patchelf, …
#   2. The STT sidecar crate (ocw-stt) — cpal needs ALSA, whisper.cpp needs a C/C++ compiler.
#
# Detects the distro from /etc/os-release and uses the right package names. Run once, then
# create the Python venv per the header of build_linux.sh. Requires sudo for the install;
# pass -y to auto-confirm the apt/distro prompt.
set -euo pipefail

ASSUME_YES=0
for a in "$@"; do
  case "$a" in -y|--yes) ASSUME_YES=1 ;; *) echo "unknown arg: $a" >&2; exit 2 ;; esac
done

if [ ! -r /etc/os-release ]; then
  echo "Cannot detect distro: /etc/os-release missing. Install the deps listed in build_linux.sh by hand." >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
DISTRO="${ID:-}"
# Family detection — handle derivatives (e.g. Linux Mint → id=linuxmint, ID_LIKE=debian).
FAMILY=""
case "$DISTRO" in
  debian|ubuntu|linuxmint|pop) FAMILY=debian ;;
  fedora|rhel|rocky|almalinux|centos) FAMILY=fedora ;;
  arch|manjaro| EndeavourOS) FAMILY=arch ;;
esac
if [ -z "$FAMILY" ]; then
  for like in ${ID_LIKE:-}; do
    case "$like" in debian|ubuntu) FAMILY=debian ;; fedora|rhel) FAMILY=fedora ;; arch) FAMILY=arch ;; esac
    [ -n "$FAMILY" ] && break
  done
fi
[ -n "$FAMILY" ] || { echo "Unsupported distro: '$DISTRO'. See build_linux.sh for the manual package list." >&2; exit 1; }

CONFIRM=""
[ "$ASSUME_YES" = 1 ] && case "$FAMILY" in debian) CONFIRM=-y ;; fedora) CONFIRM="-y" ;; arch) CONFIRM="--noconfirm" ;; esac

echo "==> detected: $DISTRO ($FAMILY)"
case "$FAMILY" in
  debian)
    PACKAGES=(
      build-essential pkg-config file curl
      libwebkit2gtk-4.1-dev libssl-dev libayatana-appindicator3-dev
      librsvg2-dev patchelf libasound2-dev
      # Build-time only: whisper-rs-sys (the STT sidecar) runs bindgen, which needs
      # libclang to parse whisper.cpp's headers. Not a runtime dependency.
      libclang-dev
    )
    echo "==> apt-get install: ${PACKAGES[*]}"
    sudo apt-get update
    sudo apt-get install -y "${PACKAGES[@]}"
    ;;
  fedora)
    PACKAGES=(
      gcc-c++ pkgconfig file curl patchelf clang-devel
      webkit2gtk4.1-devel openssl-devel libayatana-appindicator-devel
      librsvg2-devel alsa-lib-devel
    )
    echo "==> dnf install: ${PACKAGES[*]}"
    sudo dnf install -y "${PACKAGES[@]}"
    ;;
  arch)
    PACKAGES=(base-devel pkgconf file curl patchelf webkit2gtk-4.1 libayatana-appindicator librsvg alsa-lib clang)
    echo "==> pacman -S: ${PACKAGES[*]}"
    sudo pacman -S --noconfirm "${PACKAGES[@]}"
    ;;
esac

echo ""
echo "Done. Next: create the build venv and build —"
echo "  python3 -m venv .venv"
echo "  .venv/bin/pip install -e '.[bedrock]' pyinstaller typer"
echo "  cd surfaces/gui && npm ci && cd ../.."
echo "  ./packaging/build_linux.sh"
