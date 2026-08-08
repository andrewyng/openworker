"""#362 — the persistent run_shell must see the user's login-shell toolchain.

A GUI-launched server runs the agent's shell as a non-interactive /bin/bash that never
sources the user's rc files, so pyenv/nvm/sdkman/conda shims are invisible and the agent
sees a different environment than the user. `LocalExecutor` now runs a one-time
login+interactive probe at startup and folds the result into the persistent shell's env.

These tests stub the login shell with a small script that injects a known directory onto
PATH, so they assert the wiring deterministically without depending on whatever shell or
toolchain the CI machine happens to have.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from coworker.tools.shell import (
    LocalExecutor,
    _login_shell_env,
    _merge_paths,
    _parse_env_dump,
    _ENV_PROBE_END,
    _ENV_PROBE_START,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="login-shell env probe is POSIX-only (#362)"
)


def _fake_login_shell(tmp_path, shim_dir: str):
    """Write an executable that emulates `<shell> -l -i -c <cmd>`: it prepends `shim_dir`
    to PATH (as an rc file would add pyenv/nvm shims) and then runs the requested command."""
    script = tmp_path / "fake_login_shell.sh"
    script.write_text(
        "#!/bin/bash\n"
        f'export PATH="{shim_dir}:$PATH"\n'
        'cmd="${@: -1}"\n'  # last arg is the -c command
        'exec /bin/bash -c "$cmd"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


# -- pure helpers -------------------------------------------------------------------

def test_parse_env_dump_only_reads_inside_the_fence():
    text = "\n".join(
        [
            "welcome to your shell",  # rc banner before the fence — ignored
            "BEFORE=nope",
            _ENV_PROBE_START,
            "PATH=/a:/b",
            "PYENV_ROOT=/home/u/.pyenv",
            "not an assignment line",
            "1BAD=badname",  # invalid env name — ignored
            _ENV_PROBE_END,
            "AFTER=nope",  # after the fence — ignored
        ]
    )
    env = _parse_env_dump(text)
    assert env == {"PATH": "/a:/b", "PYENV_ROOT": "/home/u/.pyenv"}
    assert "BEFORE" not in env and "AFTER" not in env


def test_parse_env_dump_keeps_equals_in_value():
    text = f"{_ENV_PROBE_START}\nFOO=a=b=c\n{_ENV_PROBE_END}"
    assert _parse_env_dump(text) == {"FOO": "a=b=c"}


def test_parse_env_dump_empty_without_fence():
    assert _parse_env_dump("PATH=/a\nHOME=/h") == {}


def test_merge_paths_user_first_and_dedup():
    merged = _merge_paths("/shim:/usr/local/bin", "/usr/local/bin:/usr/bin")
    assert merged == "/shim:/usr/local/bin:/usr/bin"


def test_merge_paths_handles_missing_and_empty():
    assert _merge_paths(None, "/usr/bin") == "/usr/bin"
    assert _merge_paths("/usr/bin", None) == "/usr/bin"
    assert _merge_paths(None, None) is None
    assert _merge_paths("", "") is None


# -- the probe ----------------------------------------------------------------------

def test_login_shell_env_captures_rc_supplied_path(tmp_path):
    shim = str(tmp_path / "pyenv-shims")
    env = _login_shell_env(_fake_login_shell(tmp_path, shim))
    assert shim in env.get("PATH", "").split(os.pathsep)


def test_login_shell_env_missing_shell_returns_empty():
    assert _login_shell_env("/nonexistent/definitely/not/a/shell") == {}


def test_login_shell_env_none_when_shell_unset(monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    assert _login_shell_env() == {}


def test_login_shell_env_times_out_gracefully(tmp_path):
    slow = tmp_path / "slow_shell.sh"
    slow.write_text("#!/bin/bash\nsleep 5\n")
    slow.chmod(0o755)
    assert _login_shell_env(str(slow), timeout=0.5) == {}


# -- executor wiring ----------------------------------------------------------------

def test_executor_exposes_login_path_to_commands(tmp_path, monkeypatch):
    shim = str(tmp_path / "shims")
    monkeypatch.setenv("SHELL", _fake_login_shell(tmp_path, shim))
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10, probe_login_env=True)
    try:
        assert shim in ex._env["PATH"].split(os.pathsep)
        # and it actually reaches the live persistent shell, not just the env dict
        out = ex.run("echo $PATH")["output"]
        assert shim in out
    finally:
        ex.close()


def test_executor_probe_disabled_leaves_path_untouched(tmp_path, monkeypatch):
    shim = str(tmp_path / "shims")
    monkeypatch.setenv("SHELL", _fake_login_shell(tmp_path, shim))
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10, probe_login_env=False)
    try:
        assert shim not in ex._env["PATH"].split(os.pathsep)
    finally:
        ex.close()


def test_noninteractive_defaults_win_over_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", _fake_login_shell(tmp_path, str(tmp_path / "shims")))
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10, probe_login_env=True)
    try:
        assert ex._env["GIT_TERMINAL_PROMPT"] == "0"
        assert ex._env["PIP_NO_INPUT"] == "1"
    finally:
        ex.close()


def test_explicit_env_overrides_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", _fake_login_shell(tmp_path, str(tmp_path / "shims")))
    ex = LocalExecutor(
        cwd=tmp_path,
        default_timeout=10,
        probe_login_env=True,
        env={"PATH": "/only/this"},
    )
    try:
        assert ex._env["PATH"] == "/only/this"
    finally:
        ex.close()
