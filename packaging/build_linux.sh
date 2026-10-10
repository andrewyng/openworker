#!/usr/bin/env bash
# Build the Linux `openworker` program: one folder with the program and its support files
# (no Python needed on the machine), packed as openworker-<version>-linux-<arch>.tar.gz.
#
#   1. Inside a Debian bullseye container (glibc 2.31, so the program runs on Ubuntu 20.04,
#      Debian 11 and newer), install this package with the bedrock and openshell extras.
#   2. PyInstaller with packaging/openworker-server.spec, OPENWORKER_BUNDLE=cli: the whole
#      command line as the entry point. `openworker-server` is a link to the same program.
#   3. Pack the folder and write its SHA-256 next to it, in packaging/dist/.
#
# Prerequisites: Docker, on a Linux machine of the target architecture (x86_64 or aarch64;
# the container is native, not emulated).
#
# Install on a machine:
#   tar -xzf openworker-<version>-linux-<arch>.tar.gz -C ~/.local/share
#   ln -sf ~/.local/share/openworker/openworker ~/.local/bin/openworker
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="$HERE/dist"
IMAGE="${OPENWORKER_BUILD_IMAGE:-python:3.12-bullseye}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
ARCH="$(uname -m)"
NAME="openworker-$VERSION-linux-$ARCH"

mkdir -p "$OUT"
rm -rf "$OUT/openworker" "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256"

echo "==> [1/3] building $NAME in $IMAGE"
# The source is mounted read-only and copied inside, so the build leaves nothing in the
# checkout; only packaging/dist/ is written, owned by the calling user.
docker run --rm \
  -v "$ROOT:/src:ro" -v "$OUT:/out" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$IMAGE" bash -euo pipefail -c '
    mkdir /work && cp -a /src/coworker /src/packaging /src/pyproject.toml /src/README.md /src/LICENSE /work/
    rm -rf /work/packaging/dist /work/packaging/build
    cd /work
    python -m venv /venv
    /venv/bin/pip install -q --upgrade pip
    # typer: only for PyInstaller, which imports mcp.cli while collecting mcp.
    /venv/bin/pip install -q ".[bedrock,openshell]" pyinstaller tzdata typer
    /venv/bin/python -c "import grpc"  # OpenShell cannot be used without it
    echo "==> [2/3] PyInstaller"
    OPENWORKER_BUNDLE=cli /venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
      --distpath /work/dist --workpath /work/build packaging/openworker-server.spec
    ln -s openworker /work/dist/openworker/openworker-server
    cp -a /work/dist/openworker /out/
    chown -R "$HOST_UID:$HOST_GID" /out/openworker
  '

echo "==> [3/3] packing"
"$OUT/openworker/openworker" version
tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" openworker
(cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
ls -la "$OUT/$NAME.tar.gz"
cat "$OUT/$NAME.tar.gz.sha256"
