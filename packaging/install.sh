#!/bin/sh
# OpenWorker headless installer — Linux and macOS.
#
#   curl -fsSL https://openworker.com/install.sh | sh
#
# Installs the `openworker` command for the current user (no sudo): it uses `uv` when present,
# `pipx` when that is what the machine has, and otherwise installs `uv` first (uv brings its
# own Python, so the system's Python version does not matter). Re-run it to upgrade.
#
# Settings (environment variables):
#   OPENWORKER_VERSION   install this exact version (default: the latest)
#   OPENWORKER_PACKAGE   what to install (default: "openworker"; a wheel path or URL also works)
#
# The whole script is one function called on the last line, so a download that is cut off
# half-way runs nothing.

set -eu

main() {
    package="${OPENWORKER_PACKAGE:-openworker}"
    if [ -n "${OPENWORKER_VERSION:-}" ] && [ "$package" = "openworker" ]; then
        package="openworker==${OPENWORKER_VERSION}"
    fi

    say() { printf '%s\n' "$*"; }
    fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

    case "$(uname -s)" in
        Linux | Darwin) ;;
        *) fail "this installer supports Linux and macOS. On Windows, use the desktop app: https://openworker.com" ;;
    esac

    if [ "$(id -u)" = "0" ]; then
        say "note: running as root installs OpenWorker for root only. A normal user account is the usual choice."
    fi

    if command -v uv >/dev/null 2>&1; then
        installer="uv"
    elif command -v pipx >/dev/null 2>&1; then
        installer="pipx"
    else
        command -v curl >/dev/null 2>&1 || fail "curl is needed to fetch uv. Install curl, or install uv or pipx yourself, then run this again."
        say "Installing uv (https://docs.astral.sh/uv/) — it manages Python for OpenWorker…"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # uv's installer puts it here; this shell has not re-read its profile yet.
        PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}:$HOME/.cargo/bin:$PATH"
        export PATH
        command -v uv >/dev/null 2>&1 || fail "uv was installed but is not on PATH. Open a new terminal and run this again."
        installer="uv"
    fi

    say "Installing ${package} with ${installer}…"
    if [ "$installer" = "uv" ]; then
        uv tool install --upgrade "$package"
        bin_dir="$(uv tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")"
    else
        pipx install --force "$package"
        bin_dir="${PIPX_BIN_DIR:-$HOME/.local/bin}"
    fi

    openworker_bin="${bin_dir}/openworker"
    [ -x "$openworker_bin" ] || openworker_bin="$(command -v openworker || true)"
    [ -n "$openworker_bin" ] || fail "the install finished but the openworker command was not found."

    version_line="$("$openworker_bin" version 2>&1 || true)"
    case "$version_line" in
        *placeholder*)
            fail "the OpenWorker runtime is not published to PyPI yet (the name holds a placeholder). See https://github.com/andrewyng/openworker"
            ;;
    esac

    say ""
    say "Installed: ${version_line}"
    case ":${PATH}:" in
        *":${bin_dir}:"*) ;;
        *)
            say ""
            say "Add it to your PATH (then open a new terminal):"
            say "  export PATH=\"${bin_dir}:\$PATH\""
            ;;
    esac
    say ""
    say "Next:"
    say "  1. In the OpenWorker app: Settings > Machines > Add a machine, and copy the join link."
    say "  2. Here:  openworker join <link>"
    say "  3. To keep it running in the background:  openworker machine service install"
}

main "$@"
