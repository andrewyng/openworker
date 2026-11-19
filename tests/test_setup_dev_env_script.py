"""packaging/setup_dev_env.sh must bootstrap both venv layouts (#9).

A venv's executables live in bin/ on POSIX but in Scripts/ on Windows — and
people do run this script under Git Bash / WSL, where `python -m venv` builds
the Windows layout. The script used to hardcode $VENV/bin, so every step after
venv creation failed with "No such file or directory".

These tests run the real script end to end against a stub interpreter that
creates whichever layout we ask for, and assert the script drives the pip /
python executables that actually exist and prints the matching server path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "setup_dev_env.sh"

# Stands in for every interpreter the script may call: the bootstrap python
# found on PATH (any of its versioned names), and the python/pip it invokes
# inside the venv. `FAKE_VENV_LAYOUT` picks the layout `python -m venv` builds;
# every invocation logs "$0" so the test can see which directory was used.
STUB = """\
#!/usr/bin/env bash
echo "$0" >>"$FAKE_LOG"
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  if [ "$FAKE_VENV_LAYOUT" = "windows" ]; then
    dir="$3/Scripts"
  else
    dir="$3/bin"
  fi
  mkdir -p "$dir"
  for name in python pip openworker-server; do
    cp "$FAKE_STUB" "$dir/$name"
    chmod +x "$dir/$name"
  done
fi
exit 0
"""


def _run_script(tmp_path: Path, layout: str) -> subprocess.CompletedProcess:
    stub = tmp_path / "stub.sh"
    stub.write_text(STUB)
    stub.chmod(0o755)

    # The script resolves ROOT from its own location, so run a copy out of
    # tmp_path — the fake venv then lands in tmp_path/.venv, not in the repo.
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    shutil.copy2(REPO / "packaging" / SCRIPT_NAME, packaging / SCRIPT_NAME)

    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    # Shadow every name the discovery loop tries, so the stub is picked no
    # matter which Pythons exist on this machine.
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        (bin_dir / name).symlink_to(stub)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_VENV_LAYOUT"] = layout
    env["FAKE_LOG"] = str(tmp_path / "calls.log")
    env["FAKE_STUB"] = str(stub)

    return subprocess.run(
        ["bash", str(packaging / SCRIPT_NAME)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_posix_layout_drives_bin(tmp_path):
    result = _run_script(tmp_path, "posix")
    assert result.returncode == 0, result.stderr
    venv_bin = tmp_path / ".venv" / "bin"
    assert (venv_bin / "pip").exists()
    assert f"server: {venv_bin / 'openworker-server'}" in result.stdout
    # Every post-venv step (pip ×2, the import check) ran from bin/.
    calls = [Path(line) for line in (tmp_path / "calls.log").read_text().splitlines()]
    venv_calls = {c.name for c in calls if c.parent == venv_bin}
    assert {"pip", "python"} <= venv_calls, calls
    assert not any(c.parent == tmp_path / ".venv" / "Scripts" for c in calls)


def test_windows_layout_drives_scripts(tmp_path):
    result = _run_script(tmp_path, "windows")
    assert result.returncode == 0, result.stderr
    venv_scripts = tmp_path / ".venv" / "Scripts"
    assert (venv_scripts / "pip").exists()
    assert f"server: {venv_scripts / 'openworker-server'}" in result.stdout
    # Every post-venv step (pip ×2, the import check) ran from Scripts/.
    calls = [Path(line) for line in (tmp_path / "calls.log").read_text().splitlines()]
    venv_calls = {c.name for c in calls if c.parent == venv_scripts}
    assert {"pip", "python"} <= venv_calls, calls
    assert not any(c.parent == tmp_path / ".venv" / "bin" for c in calls)
