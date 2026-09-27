"""Render a macOS Seatbelt profile from OUR words: the session's folders and a proxy port.

The profile starts from "deny everything" and allows:
- reading the system (programs, libraries, settings), never the user's home as a whole;
- reading and writing the session's folders (a read-only folder: reading only) and one
  temporary folder that belongs to this sandbox;
- reading a fixed list of developer toolchains under the home folder, so `node`, `cargo`
  and friends still run, and the user's git settings (git refuses to run without them);
- the network only through one local port, where the server's allow-list proxy listens.
  Without a port there is no network at all.

Everything else under the home folder (`~/.ssh`, `~/.aws`, documents, other projects,
OpenWorker's own state with its keys) cannot be read, listed or written.

Seatbelt matches real paths, so every path is resolved first (`/tmp` is `/private/tmp`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence

# Programs, libraries and settings. No entry here is under a home folder.
_SYSTEM_READ = [
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
    "/opt",
    "/Applications",
    "/private/etc",
    "/private/var/db/timezone",
    "/private/var/db/xcode_select_link",
    "/private/var/select",
    "/dev",
]
# Folders that must be walkable for path lookups to work, without listing what is in them.
_LITERAL_READ = ["/", "/private", "/private/tmp", "/private/var", "/private/var/folders", "/Users", "/Volumes"]

# Developer toolchains under the home folder: readable, never writable.
TOOLCHAINS = [
    ".nvm",
    ".volta",
    ".bun",
    ".deno",
    ".pyenv",
    ".rbenv",
    ".asdf",
    ".sdkman",
    ".cargo",
    ".rustup",
    ".local/bin",
    ".local/share/uv",
    ".local/share/mise",
    ".local/pipx",
    "go",
]
# The user's git settings. Files, not folders of secrets: credentials live in the keychain.
GIT_SETTINGS = [".gitconfig", ".gitignore", ".gitignore_global", ".config/git"]

# System services a command-line program needs. Not the keychain, not the pasteboard.
_MACH_SERVICES = [
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.system.logger",
    "com.apple.system.notification_center",
    "com.apple.logd",
    "com.apple.diagnosticd",
    "com.apple.trustd",
    "com.apple.trustd.agent",
    "com.apple.bsd.dirhelper",
    "com.apple.CoreServices.coreservicesd",
    "com.apple.lsd.mapdb",
    "com.apple.cfprefsd.daemon",
    "com.apple.cfprefsd.agent",
    "com.apple.SecurityServer",
    "com.apple.ocspd",
]


_XCRUN_CACHE = r"/private/var/folders/[^/]+/[^/]+/[TC]/xcrun_db"


def real(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(paths: Sequence[str]) -> str:
    return " ".join(f"(subpath {_quote(p)})" for p in paths)


def _literals(paths: Sequence[str]) -> str:
    return " ".join(f"(literal {_quote(p)})" for p in paths)


def home_read_only(home: Optional[str] = None) -> list[str]:
    """The toolchain folders and git settings that exist under this home folder."""
    base = Path(real(home or "~"))
    return [real(base / name) for name in (*TOOLCHAINS, *GIT_SETTINGS) if (base / name).exists()]


def render(
    roots: Sequence[dict[str, Any]],
    *,
    runtime_dir: str,
    read_only: Sequence[str] = (),
    proxy_port: Optional[int] = None,
    home: Optional[str] = None,
) -> str:
    """`roots`: [{"path": absolute path, "writable": bool}]. `runtime_dir`: this sandbox's
    own folder (socket and temporary files). `read_only`: more paths to read, e.g. the
    runner file and the Python that runs it."""
    writable = [real(r["path"]) for r in roots if r.get("writable")]
    readable = [real(r["path"]) for r in roots if not r.get("writable")]
    readable += [real(p) for p in read_only] + home_read_only(home)
    runtime = real(runtime_dir)
    lines = [
        "(version 1)",
        "(deny default)",
        "; processes: start programs, and signal only processes inside this sandbox",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow signal (target same-sandbox))",
        "(allow process-info* (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow ipc-posix-shm-read-data)",
        f"(allow mach-lookup {' '.join(f'(global-name {_quote(s)})' for s in _MACH_SERVICES)})",
        "; files: names and sizes anywhere (path lookups need it), contents only where listed",
        "(allow file-read-metadata)",
        f"(allow file-read* {_subpaths(_SYSTEM_READ)} {_literals(_LITERAL_READ)})",
        f"(allow file-write-data {_literals(['/dev/null', '/dev/zero', '/dev/tty', '/dev/dtracehelper'])})",
        "(allow file-ioctl (literal \"/dev/tty\") (literal \"/dev/dtracehelper\"))",
        f"(allow file-read* file-write* {_subpaths([runtime, *writable])})",
        "; Apple's command-line shims (/usr/bin/git, /usr/bin/python3) keep a small lookup cache in",
        "; the per-user temporary folder and ignore TMPDIR; without it every call takes a second",
        f"(allow file-read* file-write* (regex #\"^{_XCRUN_CACHE}\"))",
    ]
    if readable:
        lines.append(f"(allow file-read* {_subpaths(readable)})")
    lines += [
        "; the runner's own socket lives in the sandbox's folder",
        f"(allow network-bind network-inbound network-outbound (local unix-socket (subpath {_quote(runtime)})))",
        f"(allow network-bind network-inbound network-outbound (remote unix-socket (subpath {_quote(runtime)})))",
    ]
    if proxy_port:
        lines += [
            "; the network: only the allow-list proxy on this machine",
            f'(allow network-outbound (remote tcp "localhost:{int(proxy_port)}"))',
        ]
    return "\n".join(lines) + "\n"
