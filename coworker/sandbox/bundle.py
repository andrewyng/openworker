"""Pack the tool runner into one file.

The runner is delivered to a sandbox as a read-only mount of a single zipapp, so it always
matches this server's version and no image has to carry it. The file lives in the state
directory (`<state dir>/sandbox/`), which is a real folder on the machine, so a provider can
mount that folder into a sandbox whether the server runs on the host or in a sandbox itself.
"""

from __future__ import annotations

import sys
import hashlib
import os
import zipfile
from pathlib import Path
from typing import Optional

from ..secrets import state_dir

_PACKAGE = "owrunner"  # the name the runner package has inside the zipapp
_MAIN = f"from {_PACKAGE}.__main__ import main\nraise SystemExit(main())\n"


# aisuite's file and git toolkits are plain standard-library code except for one import of
# the `tool` decorator, which only their factory functions use. The runner calls the
# toolkit classes directly, so a do-nothing stand-in is enough inside the packed file.
_AISUITE_TOOLKITS = ("files", "git")
_AGENTS_STANDIN = '''"""Stand-in for `aisuite.agents` inside the packed runner (see bundle.py)."""


class ToolMetadata:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def tool(fn=None, **_kwargs):
    return fn if fn is not None else (lambda f: f)
'''


def _sources() -> list[Path]:
    root = Path(__file__).parent / "runner"
    if getattr(sys, "frozen", False):  # the packaged app ships the sources as data (the spec)
        root = Path(getattr(sys, "_MEIPASS", "")) / "coworker" / "sandbox" / "runner_src"
    found = sorted(p for p in root.glob("*.py"))
    if not found:
        raise RuntimeError(f"the tool runner's sources are missing from this build ({root})")
    return found


def _toolkit_sources() -> list[Path]:
    import importlib.util

    found = []
    for name in _AISUITE_TOOLKITS:
        if getattr(sys, "frozen", False):  # the packaged app ships the sources as data (the spec)
            origin = Path(getattr(sys, "_MEIPASS", "")) / "aisuite" / "toolkits_src" / f"{name}.py"
        else:
            spec = importlib.util.find_spec(f"aisuite.toolkits.{name}")
            origin = Path(spec.origin) if spec is not None and spec.origin else None
        if origin is None or origin.suffix != ".py" or not origin.is_file():
            raise RuntimeError(f"cannot find the source of aisuite.toolkits.{name} to pack into the tool runner")
        found.append(origin)
    return found


def runner_dir() -> Path:
    return state_dir() / "sandbox"


def build_runner_zipapp(dest_dir: Optional[Path] = None) -> Path:
    """Build (or reuse) the runner zipapp and return its path. The name carries a hash of
    the sources, so an upgraded or edited runner never reuses a stale file."""
    sources = _sources()
    toolkits = _toolkit_sources()
    digest = hashlib.sha256(_AGENTS_STANDIN.encode())
    for src in (*sources, *toolkits):
        digest.update(src.name.encode())
        digest.update(src.read_bytes())
    dest_dir = Path(dest_dir) if dest_dir is not None else runner_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"runner-{digest.hexdigest()[:12]}.pyz"
    if target.exists():
        return target
    tmp = target.with_suffix(f".tmp{os.getpid()}")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__main__.py", _MAIN)
        for src in sources:
            zf.write(src, f"{_PACKAGE}/{src.name}")
        # `from ..agents import ...` inside the toolkits resolves to this stand-in.
        zf.writestr(f"{_PACKAGE}/agents.py", _AGENTS_STANDIN)
        zf.writestr(f"{_PACKAGE}/aisuite_toolkits/__init__.py", "")
        for src in toolkits:
            zf.write(src, f"{_PACKAGE}/aisuite_toolkits/{src.name}")
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    return target
