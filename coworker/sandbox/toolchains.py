"""Developer tools under the home folder that a sandbox may READ (design doc, Windows 3d.4;
macOS section 11 step 4).

A sandbox hides the home folder, and with it the tools people install per user: Node
versions under `.nvm`, Python versions under `.pyenv`, Cargo, Scoop. Without these the
agent has no `node`. So a fixed list of tool folders may be made readable, never writable:
all off until the user switches one on (UX-053 v6), and the user can add one, machine-level,
in Settings ▸ Sandbox
(`[[sandbox_toolchains]]` in the machine's config.toml; a project cannot change it).

Per platform. The Mac list is what the Seatbelt profile always granted; the Windows list
is the ruling of 2026-09-25. OpenShell mounts no home folder, so it has no list.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional, Sequence

# name, title, path under the home folder (forward slashes on every platform)
_MAC: list[tuple[str, str, str]] = [
    ("nvm", "nvm (Node versions)", "~/.nvm"),
    ("volta", "Volta", "~/.volta"),
    ("bun", "Bun", "~/.bun"),
    ("deno", "Deno", "~/.deno"),
    ("pyenv", "pyenv (Python versions)", "~/.pyenv"),
    ("rbenv", "rbenv", "~/.rbenv"),
    ("asdf", "asdf", "~/.asdf"),
    ("sdkman", "SDKMAN", "~/.sdkman"),
    ("cargo", "Cargo", "~/.cargo"),
    ("rustup", "rustup", "~/.rustup"),
    ("local-bin", "~/.local/bin", "~/.local/bin"),
    ("uv", "uv", "~/.local/share/uv"),
    ("mise", "mise", "~/.local/share/mise"),
    ("pipx", "pipx", "~/.local/pipx"),
    ("go", "Go", "~/go"),
]
_WINDOWS: list[tuple[str, str, str]] = [
    ("nvm", "nvm for Windows (Node versions)", "~/AppData/Roaming/nvm"),
    ("npm", "npm global packages", "~/AppData/Roaming/npm"),
    ("pyenv", "pyenv-win (Python versions)", "~/.pyenv"),
    ("python", "Python (per-user install)", "~/AppData/Local/Programs/Python"),
    ("scoop", "Scoop", "~/scoop"),
    ("cargo", "Cargo", "~/.cargo"),
    ("rustup", "rustup", "~/.rustup"),
    ("go", "Go", "~/go"),
    ("pipx", "pipx", "~/AppData/Local/pipx"),
    ("uv", "uv", "~/AppData/Local/uv"),
]


def defaults(platform: str = sys.platform) -> list[dict[str, Any]]:
    """The shipped list for a platform, every entry switched off."""
    rows = _WINDOWS if platform == "win32" else _MAC if platform == "darwin" else []
    return [{"name": n, "title": t, "path": p, "enabled": False} for n, t, p in rows]


def entries(configured: Optional[Sequence[dict[str, Any]]], platform: str = sys.platform) -> list[dict[str, Any]]:
    """The machine's list: the shipped entries with the user's edits and additions applied
    by name."""
    by_name = {e["name"]: dict(e) for e in defaults(platform)}
    for raw in configured or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        name = str(raw["name"]).strip()
        base = by_name.get(name, {"name": name, "title": name, "path": "", "enabled": True})
        merged = {**base, **{k: v for k, v in raw.items() if v is not None}}
        merged["enabled"] = bool(merged.get("enabled"))
        by_name[name] = merged
    return list(by_name.values())


def _real(path: str, home: str) -> str:
    text = str(path).replace("\\", "/")
    if text == "~" or text.startswith("~/"):
        text = os.path.join(home, *text[2:].split("/")) if len(text) > 1 else home
    return os.path.realpath(os.path.expanduser(text))


def granted(
    configured: Optional[Sequence[dict[str, Any]]], *, home: Optional[str] = None, platform: str = sys.platform
) -> list[str]:
    """Real paths of the entries that are switched on AND exist under this home folder.
    Never the home folder itself, never something outside it."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    out: list[str] = []
    for e in entries(configured, platform):
        if not e.get("enabled") or not e.get("path"):
            continue
        real = _real(str(e["path"]), home)
        if real == home or not real.startswith(home + os.sep) or not os.path.exists(real):
            continue
        if real not in out:
            out.append(real)
    return out


def for_display(configured: Optional[Sequence[dict[str, Any]]], *, home: Optional[str] = None, platform: str = sys.platform) -> list[dict[str, Any]]:
    """The entries with `exists` (on this machine) and `shipped` (in the default list). A
    shipped folder this machine does not have is left out: there is nothing to show."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    shipped = {e["name"] for e in defaults(platform)}
    rows = [
        {**e, "exists": bool(e.get("path")) and os.path.exists(_real(str(e["path"]), home)), "shipped": e["name"] in shipped}
        for e in entries(configured, platform)
    ]
    return [r for r in rows if r["exists"] or not r["shipped"]]
