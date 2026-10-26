"""Credential grants: files and folders from the home folder a user chooses to share with
a sandbox.

Design doc rulings 30 to 33, section 11b and 11d ruling 6. Nothing is shared unless the user
switched an entry on in the MACHINE config (`[[sandbox_credentials]]`); a repository's own
config cannot add one. An entry is a single file or a whole folder, the user's choice; the
path decides which. Each granted entry is COPIED into the sandbox's private folder for the
session (tools like `ssh`, `aws` and `gh` write beside their credentials, and Windows
OpenSSH refuses a key another user can read), and the copy dies with the sandbox. The real
files are never opened for writing. Each entry names the hosts its tool needs; those join
the session's network allow list. Each entry carries a label: `credential` (a secret is
inside) or `configuration` (host names, profiles, options; no secret).

Logins kept in a keychain or credential manager are not files and cannot be shared.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

CREDENTIAL = "credential"
CONFIGURATION = "configuration"
LABELS = (CREDENTIAL, CONFIGURATION)

# What ships: the presets of the page's "A CLI's login" picker (UX-053 v5). None is on, or
# even listed, until the user adds it. `hosts` are "host:port"; a port of 22 is an SSH
# tunnel through the proxy. `.ssh` and `gh` are folders (their tools write beside the
# files); AWS profiles and AWS credentials are two entries, so the keys stay out unless the
# user adds them; the kubeconfig is one file. `env`: variables the tool reads to find its
# copy, `{path}` being the copy's path inside and `{dir}` the folder holding it.
# `windows_path`: where the tool keeps it on Windows, when that differs.
DEFAULT_ENTRIES: list[dict[str, Any]] = [
    {
        "name": "ssh",
        "title": "SSH keys",
        "path": "~/.ssh",
        "hosts": ["github.com:22", "gitlab.com:22"],
        "does": "push and pull over SSH, and log in to servers, as you",
        "label": CREDENTIAL,
        "enabled": False,
    },
    {
        "name": "gh",
        "title": "GitHub CLI",
        "path": "~/.config/gh",
        "hosts": ["api.github.com:443", "github.com:443"],
        "does": "use gh as you: pull requests, issues, releases",
        "label": CREDENTIAL,
        "enabled": False,
    },
    {
        "name": "aws",
        "title": "AWS profiles",
        "path": "~/.aws/config",
        "hosts": ["*.amazonaws.com:443"],
        "does": "use aws with your profiles (regions and profile names; no keys unless you add ~/.aws/credentials)",
        "label": CONFIGURATION,
        "enabled": False,
    },
    {
        "name": "kube",
        "title": "Kubernetes",
        "path": "~/.kube/config",
        "hosts": [],  # read from the kubeconfig at grant time
        "does": "use kubectl with your clusters",
        "label": CREDENTIAL,
        "enabled": False,
    },
    {
        "name": "aws-credentials",
        "title": "AWS credentials",
        "path": "~/.aws/credentials",
        "hosts": ["*.amazonaws.com:443"],
        "does": "use aws with your access keys",
        "label": CREDENTIAL,
        "enabled": False,
    },
    {
        "name": "npm",
        "title": "npm",
        "path": "~/.npmrc",
        "hosts": ["registry.npmjs.org:443"],
        "does": "install and publish private packages as you",
        "label": CREDENTIAL,
        "env": {"NPM_CONFIG_USERCONFIG": "{path}"},
        "enabled": False,
    },
    {
        "name": "docker",
        "title": "Docker registries",
        "path": "~/.docker/config.json",
        "hosts": ["registry-1.docker.io:443", "auth.docker.io:443", "production.cloudflare.docker.com:443", "ghcr.io:443"],
        "does": "push and pull images with the logins saved in this file",
        "label": CREDENTIAL,
        "env": {"DOCKER_CONFIG": "{dir}"},
        "enabled": False,
    },
    {
        "name": "gcloud",
        "title": "gcloud",
        "path": "~/.config/gcloud",
        "windows_path": "~/AppData/Roaming/gcloud",
        "hosts": ["*.googleapis.com:443", "accounts.google.com:443"],
        "does": "use gcloud with your accounts and projects",
        "label": CREDENTIAL,
        "env": {"CLOUDSDK_CONFIG": "{path}"},
        "enabled": False,
    },
    {
        "name": "terraform",
        "title": "Terraform Cloud",
        "path": "~/.terraform.d/credentials.tfrc.json",
        "windows_path": "~/AppData/Roaming/terraform.d/credentials.tfrc.json",
        "hosts": ["app.terraform.io:443", "registry.terraform.io:443", "releases.hashicorp.com:443"],
        "does": "run terraform against Terraform Cloud as you",
        "label": CREDENTIAL,
        "enabled": False,
    },
]
for _entry in DEFAULT_ENTRIES:  # a shipped entry's path is the one for this platform
    if sys.platform == "win32" and _entry.get("windows_path"):
        _entry["path"] = _entry["windows_path"]
    _entry.pop("windows_path", None)

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
    label: str = CREDENTIAL  # credential | configuration
    kind: str = "folder"  # file | folder, from what the path is on this machine
    env: dict[str, str] = field(default_factory=dict)  # templates: {path}, {dir}


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


def listed(configured: Optional[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The entries the user added (UX-053 v5): those named in the machine's config, in
    that order, whether switched on or not. A shipped entry nobody added is a preset only."""
    names = []
    for raw in configured or []:
        name = str(raw.get("name") or "").strip() if isinstance(raw, dict) else ""
        if name and name not in names:
            names.append(name)
    by_name = {e["name"]: e for e in entries(configured)}
    return [by_name[n] for n in names if n in by_name]


def entries(configured: Optional[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The machine's list: the shipped entries, with the user's edits and additions applied
    by name. Unknown keys in a user entry are kept, missing ones default."""
    by_name = {e["name"]: dict(e) for e in DEFAULT_ENTRIES}
    for raw in configured or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        name = str(raw["name"]).strip()
        base = by_name.get(name, {"name": name, "title": name, "path": "", "hosts": [], "does": "", "label": CREDENTIAL, "enabled": False})
        merged = {**base, **{k: v for k, v in raw.items() if v is not None}}
        merged["hosts"] = [str(h) for h in (merged.get("hosts") or [])]
        merged["enabled"] = bool(merged.get("enabled"))
        merged["label"] = merged.get("label") if merged.get("label") in LABELS else CREDENTIAL
        by_name[name] = merged
    return list(by_name.values())


def kind_of(path: str, *, home: Optional[str] = None) -> str:
    """"file", "folder", or "" when the path does not exist on this machine."""
    if not path:
        return ""
    real = _real(str(path), home or os.path.expanduser("~"))
    if os.path.isdir(real):
        return "folder"
    if os.path.isfile(real):
        return "file"
    return ""


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
        out.append(
            Grant(
                name=str(e["name"]), title=str(e.get("title") or e["name"]), path=real, relative=relative, hosts=hosts,
                does=str(e.get("does") or ""), label=str(e.get("label") or CREDENTIAL), kind="folder" if os.path.isdir(real) else "file",
                env={str(k): str(v) for k, v in (e.get("env") or {}).items()} if isinstance(e.get("env"), dict) else {},
            )
        )  # fmt: skip
    return out


def _kubeconfig_hosts(kube_path: str) -> list[str]:
    """`server:` lines of the kubeconfig (the file, or a folder holding `config`), as
    host:port. Plain text scan, no YAML needed."""
    from urllib.parse import urlsplit

    hosts: list[str] = []
    for path in ([os.path.join(kube_path, "config")] if os.path.isdir(kube_path) else [kube_path]):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
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
    inside_home: Optional[str] = None,
    windows: Optional[bool] = None,
) -> CopiedCredentials:
    """Make the sandbox's private home under `runtime_dir` with the granted files inside.
    Returns the environment the sandbox needs so each tool finds its copy. With no grants
    the home holds only git's settings.

    `ssh_proxy_command`: when the sandbox can only reach the network through the proxy, a
    `ProxyCommand` for ssh (with `%h` and `%p`), put first in the copied ssh config so it wins.
    `inside_home`: the path the copy will have from the sandbox's side when a provider moves
    it there after this (the Windows account's own profile); every path written into the
    copy and into the environment uses it. Default: the copy stays where it is made.
    `windows`: write Windows wrappers (`ssh.cmd`, `ssh.exe`); default: this platform.
    """
    home = os.path.realpath(home or os.path.expanduser("~"))
    sandbox_home = os.path.join(os.path.realpath(runtime_dir), "home")
    if sandbox_home == home or sandbox_home.startswith(home + os.sep) and os.path.realpath(runtime_dir) == home:
        raise ValueError("the sandbox's runtime folder cannot be the home folder itself")
    seen = sandbox_home if inside_home is None else inside_home
    on_windows = (sys.platform == "win32") if windows is None else windows
    os.makedirs(sandbox_home, mode=stat.S_IRWXU, exist_ok=True)
    for rel in _GIT_SETTINGS:
        src = os.path.join(home, rel)
        if os.path.exists(src):
            _copy_private(src, os.path.join(sandbox_home, rel))
    env: dict[str, str] = {"HOME": seen}
    hosts: list[str] = []
    path_dirs: list[str] = []
    for g in grants:
        dst = os.path.join(sandbox_home, g.relative)
        inside = os.path.join(seen, g.relative)
        _copy_private(g.path, dst)
        hosts += g.hosts
        if g.name == "ssh":
            # The wrapper lives INSIDE the sandbox home, which every provider carries in.
            bin_dir = os.path.join(sandbox_home, "bin")
            env.update(_prepare_ssh(dst, bin_dir, ssh_proxy_command, inside_dir=inside, windows=on_windows))
            env["OPENWORKER_PATH_PREPEND"] = os.path.join(seen, "bin")  # the runner daemon puts it first on PATH
            path_dirs.append(os.path.join(seen, "bin"))
        elif g.name == "gh":
            env["GH_CONFIG_DIR"] = inside
        elif g.name == "aws":
            # The shipped entry is the profiles file; a user may point it at the folder.
            if g.kind == "folder":
                env["AWS_CONFIG_FILE"] = os.path.join(inside, "config")
                env["AWS_SHARED_CREDENTIALS_FILE"] = os.path.join(inside, "credentials")
            else:
                env["AWS_CONFIG_FILE"] = inside
        elif g.name == "aws-credentials":
            env["AWS_SHARED_CREDENTIALS_FILE"] = inside
        elif g.name == "kube":
            env["KUBECONFIG"] = os.path.join(inside, "config") if g.kind == "folder" else inside
        for variable, template in g.env.items():
            env[variable] = template.replace("{path}", inside).replace("{dir}", os.path.dirname(inside))
    return CopiedCredentials(home=sandbox_home, env=env, hosts=sorted(set(hosts)), grants=list(grants), path_dirs=path_dirs)


_WINDOWS_SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"


def _prepare_ssh(ssh_dir: str, bin_dir: str, proxy_command: Optional[str], *, inside_dir: Optional[str] = None, windows: bool = False) -> dict[str, str]:
    """Make the copied `.ssh` usable from inside the sandbox.

    ssh reads its config and expands `~` from the ACCOUNT's home folder, not from `$HOME`,
    so a copy under another path is ignored unless ssh is told about it. So: a `Host *`
    block goes at the top of the copied config (ssh takes the first value it finds for an
    option) naming the copied known_hosts and every private key in the copy, with no agent
    (the real agent's socket is not reachable from inside, and sockets are not copied);
    a tiny `ssh` wrapper first on PATH passes `-F <copy>/config`; and `GIT_SSH_COMMAND` does
    the same for git. With `proxy_command`, connections go through the proxy. `inside_dir`
    is the copy's path as the sandbox will see it (default: as it is here)."""
    seen = inside_dir or ssh_dir
    config = os.path.join(ssh_dir, "config")
    existing = Path(config).read_text(encoding="utf-8", errors="replace") if os.path.exists(config) else ""
    keys = sorted(
        name for name in os.listdir(ssh_dir) if os.path.isfile(os.path.join(ssh_dir, name + ".pub")) and os.path.isfile(os.path.join(ssh_dir, name))
    )
    lines = ["# Added by OpenWorker: this is a copy of your .ssh inside the sandbox", "Host *"]
    if proxy_command:
        lines.append(f"  ProxyCommand {proxy_command}")
    lines.append(f"  UserKnownHostsFile {_ssh_path(os.path.join(seen, 'known_hosts'), windows)}")
    lines.append("  IdentityAgent none")
    lines.append("  AddKeysToAgent no")
    lines += [f"  IdentityFile {_ssh_path(os.path.join(seen, key), windows)}" for key in keys]
    Path(config).write_text("\n".join(lines) + "\n\n" + existing, encoding="utf-8")
    os.chmod(config, stat.S_IRUSR | stat.S_IWUSR)
    Path(os.path.join(ssh_dir, "known_hosts")).touch(mode=stat.S_IRUSR | stat.S_IWUSR)
    os.makedirs(bin_dir, mode=stat.S_IRWXU, exist_ok=True)
    seen_config = os.path.join(seen, "config")
    if windows:
        wrapper = os.path.join(bin_dir, "ssh.cmd")
        Path(wrapper).write_text(f'@"{_WINDOWS_SSH}" -F "{seen_config}" %*\r\n', encoding="utf-8")
        return {"GIT_SSH_COMMAND": f'"{_WINDOWS_SSH}" -F "{seen_config}"'}
    wrapper = os.path.join(bin_dir, "ssh")
    Path(wrapper).write_text(f'#!/bin/sh\nexec /usr/bin/ssh -F "{seen_config}" "$@"\n', encoding="utf-8")
    os.chmod(wrapper, stat.S_IRWXU)
    return {"GIT_SSH_COMMAND": f'/usr/bin/ssh -F "{seen_config}"'}


def _ssh_path(path: str, windows: bool) -> str:
    """A path in an ssh config: quoted when it has spaces; forward slashes on Windows, which
    Windows OpenSSH reads and which keeps backslashes from being taken as escapes."""
    if windows:
        path = path.replace("\\", "/")
    return f'"{path}"' if " " in path else path


def mac_ssh_proxy_command(port: int) -> str:
    """macOS ships BSD nc, which speaks HTTP CONNECT."""
    return f"/usr/bin/nc -X connect -x 127.0.0.1:{int(port)} %h %p"


def windows_ssh_proxy_command(python: str, runner: str, port: int) -> str:
    """Windows ships no nc: the runner's own `connect` command is the ProxyCommand."""
    return f'"{python}" -S "{runner}" connect 127.0.0.1 {int(port)} %h %p'


def context_lines(copied: Optional[CopiedCredentials]) -> str:
    """What the agent is told about the credentials it was given (ruling 21: the agent knows
    its environment). Empty when nothing was granted."""
    if copied is None or not copied.grants:
        return ""
    lines = ["Credentials shared with this sandbox (copies, deleted when the session ends):"]
    for g in copied.grants:
        what = "folder" if g.kind == "folder" else "file"
        lines.append(f"- {g.title} ({g.relative}, {what}): you can {g.does}.")
    return "\n".join(lines)
