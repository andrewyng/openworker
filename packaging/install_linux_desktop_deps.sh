#!/usr/bin/env bash
# Install Ubuntu packages required to build/run the Tauri desktop shell.
#
# Tested on Ubuntu 24.04. The browser/dev UI does not need these native packages,
# but `npm run tauri dev` and `packaging/build_linux.sh` do.
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This helper supports apt-based Ubuntu/Debian systems only." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  curl \
  file \
  libasound2-dev \
  libayatana-appindicator3-dev \
  libclang-dev \
  librsvg2-dev \
  libsoup-3.0-dev \
  libssl-dev \
  libwebkit2gtk-4.1-dev \
  libxdo-dev \
  pkg-config \
  wget
