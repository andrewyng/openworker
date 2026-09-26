"""How a provider starts the tool runner on THIS machine.

From a source or pip install the runner is the zipapp, run by the same Python that runs the
server, with `-S` so no site-packages leak in. In the packaged desktop app there is no
Python to call: the frozen sidecar starts ITSELF in runner mode (`openworker-server
sandbox-runner ...`), which loads only the standard-library runner package. Providers that
run the runner inside a Linux container (OpenShell) keep using the zipapp, because the
Mac binary cannot run there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runner_command(runner_path: Path) -> list[str]:
    """The argv prefix that runs the runner here; `serve ...` or `attach ...` follows."""
    if frozen():
        return [sys.executable, "sandbox-runner"]
    return [sys.executable, "-S", str(runner_path)]


def read_paths() -> list[str]:
    """What the process that runs the runner must be able to read: the interpreter and its
    standard library, or the frozen app's folder (the bootloader reads `_internal`)."""
    paths = {os.path.dirname(os.path.realpath(sys.executable))}
    if frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            paths.add(os.path.realpath(meipass))
    else:
        paths.update({sys.prefix, sys.base_prefix})
    return sorted(p for p in paths if p)


def maybe_run_runner(argv: list[str]) -> bool:
    """`openworker-server sandbox-runner <serve|attach> ...`: run the runner and exit.
    Returns False when argv is anything else. Imports only the runner package, so the
    daemon inside a sandbox stays small and starts fast."""
    if argv[:1] != ["sandbox-runner"]:
        return False
    from .runner.__main__ import main as runner_main

    raise SystemExit(runner_main(argv[1:]))
