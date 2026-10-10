#!/bin/sh
# OpenWorker headless installer — Linux and macOS.
#
#   curl -fsSL https://openworker.com/install.sh | sh
#
# Installs the `openworker` command for the current user (no sudo). Re-run it to upgrade.
#
# On Linux (x86_64, aarch64; glibc 2.31 or newer) and on macOS (Apple Silicon, Intel) it
# downloads the `openworker` program from the GitHub release, checks its SHA-256, unpacks it
# into ~/.local/share/openworker and links it into ~/.local/bin. No Python is needed.
#
# Elsewhere (other Linux) it installs the Python package: with `uv` when present,
# `pipx` when that is what the machine has, and otherwise it installs `uv` first (uv brings
# its own Python, so the system's Python version does not matter).
#
# Settings (environment variables):
#   OPENWORKER_VERSION       install this exact version (default: the latest)
#   OPENWORKER_INSTALL       "program" or "python" to choose the route (default: automatic)
#   OPENWORKER_PACKAGE       the Python route: what to install (default: "openworker";
#                            a wheel path or URL also works)
#   OPENWORKER_DOWNLOAD_URL  the program route: where the release files are (default: the
#                            GitHub release; for mirrors and tests)
#
# The whole script is one function called on the last line, so a download that is cut off
# half-way runs nothing.

set -eu

main() {
    repo="https://github.com/andrewyng/openworker"

    say() { printf '%s\n' "$*"; }
    fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

    case "$(uname -s)" in
        Linux | Darwin) ;;
        *) fail "this installer supports Linux and macOS. On Windows, use the desktop app: https://openworker.com" ;;
    esac

    if [ "$(id -u)" = "0" ]; then
        say "note: running as root installs OpenWorker for root only. A normal user account is the usual choice."
    fi

    route="${OPENWORKER_INSTALL:-}"
    if [ -z "$route" ]; then
        if [ -n "${OPENWORKER_PACKAGE:-}" ] || ! program_runs_here; then route="python"; else route="program"; fi
    fi
    case "$route" in
        program) install_program ;;
        python) install_python ;;
        *) fail "OPENWORKER_INSTALL must be \"program\" or \"python\", not \"$route\"" ;;
    esac

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

# The program is built for macOS on Apple Silicon and Intel, and for Linux with glibc 2.31
# or newer on x86_64 and aarch64.
program_runs_here() {
    if [ "$(uname -s)" = "Darwin" ]; then
        case "$(uname -m)" in arm64 | x86_64) return 0 ;; *) return 1 ;; esac
    fi
    [ "$(uname -s)" = "Linux" ] || return 1
    case "$(uname -m)" in x86_64 | aarch64) ;; *) return 1 ;; esac
    glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
    [ -n "$glibc" ] || return 1  # musl (Alpine) and other C libraries
    major="${glibc%%.*}"
    minor="${glibc#*.}"
    minor="${minor%%.*}"
    [ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 31 ]; }
}

install_program() {
    arch="$(uname -m)"
    case "$(uname -s)" in Darwin) os="macos" ;; *) os="linux" ;; esac
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download OpenWorker. Install curl, then run this again."
    command -v tar >/dev/null 2>&1 || fail "tar is needed to unpack OpenWorker."
    if command -v sha256sum >/dev/null 2>&1; then
        sha() { sha256sum "$1" | awk '{print $1}'; }
    elif command -v shasum >/dev/null 2>&1; then
        sha() { shasum -a 256 "$1" | awk '{print $1}'; }
    else
        fail "sha256sum (or shasum) is needed to check the download."
    fi

    if [ -n "${OPENWORKER_VERSION:-}" ]; then
        file="openworker-${OPENWORKER_VERSION}-${os}-${arch}.tar.gz"
        base="${OPENWORKER_DOWNLOAD_URL:-$repo/releases/download/v${OPENWORKER_VERSION}}"
    else
        file="openworker-${os}-${arch}.tar.gz"  # the stable name of the latest release
        base="${OPENWORKER_DOWNLOAD_URL:-$repo/releases/latest/download}"
    fi

    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    say "Downloading ${base}/${file}…"
    curl -fL --progress-bar -o "$tmp/$file" "$base/$file" || fail "could not download $base/$file"
    curl -fsSL -o "$tmp/$file.sha256" "$base/$file.sha256" || fail "could not download the checksum $base/$file.sha256"
    expected="$(awk '{print $1}' "$tmp/$file.sha256")"
    actual="$(sha "$tmp/$file")"
    [ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "the download does not match its checksum (expected ${expected:-nothing}, got $actual). Nothing was installed."
    say "Checksum OK."

    mkdir -p "$tmp/unpacked"
    tar -xzf "$tmp/$file" -C "$tmp/unpacked"
    [ -x "$tmp/unpacked/openworker/openworker" ] || fail "the download does not contain the openworker program. Nothing was installed."

    # Swap the folder in whole. A running `openworker up` keeps the files it has open, and
    # uses the new version once it is restarted.
    data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    dest="$data_home/openworker"
    bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
    mkdir -p "$data_home" "$bin_dir"
    rm -rf "$dest.new" "$dest.old"
    mv "$tmp/unpacked/openworker" "$dest.new"
    [ ! -e "$dest" ] || mv "$dest" "$dest.old"
    mv "$dest.new" "$dest"
    rm -rf "$dest.old"

    link="$bin_dir/openworker"
    if [ -e "$link" ] && [ ! -L "$link" ]; then
        say "note: replacing the earlier $link (a Python install). Remove that install with \`uv tool uninstall openworker\` or \`pipx uninstall openworker\`."
    fi
    ln -sfn "$dest/openworker" "$link"

    say ""
    say "Installed: $("$link" version 2>&1 || true) in $dest"
    if command -v systemctl >/dev/null 2>&1; then
        units="$(systemctl --user list-units --all --plain --no-legend 'openworker-*.service' 2>/dev/null | awk '{print $1}')"
        for unit in $units; do
            say "Restart the running machine to use it:  systemctl --user restart $unit"
        done
    elif command -v launchctl >/dev/null 2>&1; then
        labels="$(launchctl list 2>/dev/null | awk '$3 ~ /^com\.openworker\.machine\./ {print $3}')"
        for label in $labels; do
            say "Restart the running machine to use it:  launchctl kickstart -k gui/$(id -u)/$label"
        done
    fi
}

install_python() {
    package="${OPENWORKER_PACKAGE:-openworker}"
    if [ -n "${OPENWORKER_VERSION:-}" ] && [ "$package" = "openworker" ]; then
        package="openworker==${OPENWORKER_VERSION}"
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
}

main "$@"
