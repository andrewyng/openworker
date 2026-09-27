"""Credential grants: files from the home folder a user chooses to share with a sandbox.

Design doc rulings 30 to 33 and section 11b. Nothing is shared unless the user switched an
entry on in the MACHINE config (`[[sandbox_credentials]]`); a repository's own config cannot
add one. Each granted entry is COPIED into the sandbox's private folder for the session
(tools like `ssh`, `aws` and `gh` write beside their credentials, and Windows OpenSSH refuses
a key another user can read), and the copy dies with the sandbox. The real files are never
opened for writing. Each entry also names the hosts its tool needs; those join the session's
network allow list.

Logins kept in a keychain or credential manager are not files and cannot be shared.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

# What ships. `hosts` are "host:port"; a port of 22 is an SSH tunnel through the proxy.
DEFAULT_ENTRIES: list[dict[str, Any]] = [
    {
        "name": "ssh",
        "title": "SSH keys",
        "path": "~/.ssh",
        "hosts": ["github.com:22", "gitlab.com:22"],
        "does": "push and pull over SSH, and log in to servers, as you",
        "enabled": False,
    },
    {
        "name": "gh",
        "title": "GitHub CLI",
        "path": "~/.config/gh",
        "hosts": ["api.github.com:443", "github.com:443"],
        "does": "use gh as you: pull requests, issues, releases",
        "enabled": False,
    },
    {
        "name": "aws",
        "title": "AWS",
        "path": "~/.aws",
        "hosts": ["*.amazonaws.com:443"],
        "does": "use aws with your profiles",
        "enabled": False,
    },
    {
        "name": "kube",
        "title": "Kubernetes",
        "path": "~/.kube",
        "hosts": [],  # read from the kubeconfig at grant time
        "does": "use kubectl with your clusters",
        "enabled": False,
    },
]

# Git's own settings are not a credential, but a sandbox whose HOME is redirected must still
# see them (git refuses to commit without an identity). Always copied when present.
_GIT_SETTINGS = [".gitconfig", ".gitignore_global", ".config/git"]


@dataclass
class Grant:
    name: str
    title: str
    path: str  # the real, absolute path
    relative: str  # the path under the home folder, e.g. ".ssh"
    hosts: list[str] = field(default_factory=list)
    does: str = ""


@dataclass
class CopiedCredentials:
    """What copy_in produced: the sandbox's home folder, environment to set, hosts to allow."""

    home: str
    env: dict[str, str]
    hosts: list[str]
    grants: list[Grant]
    path_dirs: list[str] = field(default_factory=list)  # to put FIRST on the sandbox's PATH

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": g.name, "title": g.title, "path": g.path, "does": g.does} for g in self.grants]


def entries(configured: Optional[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The machine's list: the shipped entries, with the user's edits and additions applied
    by name. Unknown keys in a user entry are kept, missing ones default."""
    by_name = {e["name"]: dict(e) for e in DEFAULT_ENTRIES}
    for raw in configured or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        name = str(raw["name"]).strip()
        base = by_name.get(name, {"name": name, "title": name, "path": "", "hosts": [], "does": "", "enabled": False})
        merged = {**base, **{k: v for k, v in raw.items() if v is not None}}
        merged["hosts"] = [str(h) for h in (merged.get("hosts") or [])]
        merged["enabled"] = bool(merged.get("enabled"))
        by_name[name] = merged
    return list(by_name.values())


def _real(path: str, home: str) -> str:
    text = str(path)
    if text == "~" or text.startswith("~/"):
        text = os.path.join(home, text[2:]) if len(text) > 1 else home
    return os.path.realpath(os.path.expanduser(text))


def granted(configured: Optional[Sequence[dict[str, Any]]], *, home: Optional[str] = None) -> list[Grant]:
    """The entries that are switched on AND exist on this machine. An enabled entry whose
    file is missing is skipped, not an error: the user has no such credential here."""
    home = home or os.path.expanduser("~")
    out: list[Grant] = []
    for e in entries(configured):
        if not e.get("enabled") or not e.get("path"):
            continue
        real = _real(str(e["path"]), home)
        if not os.path.exists(real):
            continue
        home_real = os.path.realpath(home)
        if real == home_real or not real.startswith(home_real + os.sep):
            continue  # never the home folder itself, never something outside it
        relative = os.path.relpath(real, home_real)
        hosts = list(e.get("hosts") or [])
        if e["name"] == "kube" and not hosts:
            hosts = _kubeconfig_hosts(real)
        out.append(Grant(name=str(e["name"]), title=str(e.get("title") or e["name"]), path=real, relative=relative, hosts=hosts, does=str(e.get("does") or "")))
    return out


def _kubeconfig_hosts(kube_dir: str) -> list[str]:
    """`server:` lines of the kubeconfig, as host:port. Plain text scan, no YAML needed."""
    from urllib.parse import urlsplit

    hosts: list[str] = []
    for name in ("config",):
        try:
            text = Path(kube_dir, name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("server:"):
                url = urlsplit(line.split(":", 1)[1].strip().strip("'\""))
                if url.hostname:
                    hosts.append(f"{url.hostname}:{url.port or (443 if url.scheme == 'https' else 80)}")
    return sorted(set(hosts))


def _copy_private(src: str, dst: str) -> None:
    """Copy a file or a folder, owner-only. Only regular files and folders are copied:
    sockets (an ssh agent keeps one under `.ssh`), pipes and devices are skipped, and a
    symlink is copied as the file it points to when that file is a regular file."""
    if os.path.isdir(src):
        os.makedirs(dst, mode=stat.S_IRWXU, exist_ok=True)
        os.chmod(dst, stat.S_IRWXU)
        for name in os.listdir(src):
            _copy_private(os.path.join(src, name), os.path.join(dst, name))
    elif os.path.isfile(src):  # follows a symlink; False for sockets, pipes, devices
        os.makedirs(os.path.dirname(dst), mode=stat.S_IRWXU, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, stat.S_IRUSR | stat.S_IWUSR)


def copy_in(
    grants: Sequence[Grant],
    runtime_dir: str,
    *,
    home: Optional[str] = None,
    proxy_port: Optional[int] = None,
    ssh_proxy_command: Optional[str] = None,
) -> CopiedCredentials:
    """Make the sandbox's private home under `runtime_dir` with the granted files inside.
    Returns the environment the sandbox needs so each tool finds its copy. With no grants
    the home holds only git's settings.

    `ssh_proxy_command`: when the sandbox can only reach the network through the proxy, a
    `ProxyCommand` for ssh (with `%h` and `%p`), put first in the copied ssh config so it wins.
    """
    home = os.path.realpath(home or os.path.expanduser("~"))
    sandbox_home = os.path.join(os.path.realpath(runtime_dir), "home")
    if sandbox_home == home or sandbox_home.startswith(home + os.sep) and os.path.realpath(runtime_dir) == home:
        raise ValueError("the sandbox's runtime folder cannot be the home folder itself")
    os.makedirs(sandbox_home, mode=stat.S_IRWXU, exist_ok=True)
    for rel in _GIT_SETTINGS:
        src = os.path.join(home, rel)
        if os.path.exists(src):
            _copy_private(src, os.path.join(sandbox_home, rel))
    env: dict[str, str] = {"HOME": sandbox_home}
    hosts: list[str] = []
    path_dirs: list[str] = []
    for g in grants:
        dst = os.path.join(sandbox_home, g.relative)
        _copy_private(g.path, dst)
        hosts += g.hosts
        if g.name == "ssh":
            # The wrapper lives INSIDE the sandbox home, which every provider carries in.
            bin_dir = os.path.join(sandbox_home, "bin")
            env.update(_prepare_ssh(dst, bin_dir, ssh_proxy_command))
            env["OPENWORKER_PATH_PREPEND"] = bin_dir  # the runner daemon puts it first on PATH
            path_dirs.append(bin_dir)
        elif g.name == "gh":
            env["GH_CONFIG_DIR"] = dst
        elif g.name == "aws":
            env["AWS_CONFIG_FILE"] = os.path.join(dst, "config")
            env["AWS_SHARED_CREDENTIALS_FILE"] = os.path.join(dst, "credentials")
        elif g.name == "kube":
            env["KUBECONFIG"] = os.path.join(dst, "config")
    return CopiedCredentials(home=sandbox_home, env=env, hosts=sorted(set(hosts)), grants=list(grants), path_dirs=path_dirs)


def _prepare_ssh(ssh_dir: str, bin_dir: str, proxy_command: Optional[str]) -> dict[str, str]:
    """Make the copied `.ssh` usable from inside the sandbox.

    ssh reads its config and expands `~` from the ACCOUNT's home folder, not from `$HOME`,
    so a copy under another path is ignored unless ssh is told about it. So: a `Host *`
    block goes at the top of the copied config (ssh takes the first value it finds for an
    option) naming the copied known_hosts and every private key in the copy, with no agent
    (the real agent's socket is not reachable from inside, and sockets are not copied);
    a tiny `ssh` wrapper first on PATH passes `-F <copy>/config`; and `GIT_SSH_COMMAND` does
    the same for git. With `proxy_command`, connections go through the proxy."""
    config = os.path.join(ssh_dir, "config")
    existing = Path(config).read_text(encoding="utf-8", errors="replace") if os.path.exists(config) else ""
    keys = sorted(
        os.path.join(ssh_dir, name)
        for name in os.listdir(ssh_dir)
        if os.path.isfile(os.path.join(ssh_dir, name + ".pub")) and os.path.isfile(os.path.join(ssh_dir, name))
    )
    lines = ["# Added by OpenWorker: this is a copy of your .ssh inside the sandbox", "Host *"]
    if proxy_command:
        lines.append(f"  ProxyCommand {proxy_command}")
    lines.append(f"  UserKnownHostsFile {os.path.join(ssh_dir, 'known_hosts')}")
    lines.append("  IdentityAgent none")
    lines.append("  AddKeysToAgent no")
    lines += [f"  IdentityFile {key}" for key in keys]
    Path(config).write_text("\n".join(lines) + "\n\n" + existing, encoding="utf-8")
    os.chmod(config, stat.S_IRUSR | stat.S_IWUSR)
    Path(os.path.join(ssh_dir, "known_hosts")).touch(mode=stat.S_IRUSR | stat.S_IWUSR)
    os.makedirs(bin_dir, mode=stat.S_IRWXU, exist_ok=True)
    wrapper = os.path.join(bin_dir, "ssh")
    Path(wrapper).write_text(f'#!/bin/sh\nexec /usr/bin/ssh -F "{config}" "$@"\n', encoding="utf-8")
    os.chmod(wrapper, stat.S_IRWXU)
    return {"GIT_SSH_COMMAND": f'/usr/bin/ssh -F "{config}"'}


def mac_ssh_proxy_command(port: int) -> str:
    """macOS ships BSD nc, which speaks HTTP CONNECT."""
    return f"/usr/bin/nc -X connect -x 127.0.0.1:{int(port)} %h %p"


def context_lines(copied: Optional[CopiedCredentials]) -> str:
    """What the agent is told about the credentials it was given (ruling 21: the agent knows
    its environment). Empty when nothing was granted."""
    if copied is None or not copied.grants:
        return ""
    lines = ["Credentials shared with this sandbox (copies, deleted when the session ends):"]
    for g in copied.grants:
        lines.append(f"- {g.title} ({g.relative}): you can {g.does}.")
    return "\n".join(lines)
