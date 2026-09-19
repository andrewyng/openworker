"""`run_shell` must not hand OpenWorker's own sidecar token to the commands it runs.

`COWORKER_API_TOKEN` authenticates every request to the local API
(`server/app.py::require_sidecar_token`). That token is the only thing standing between a
process on this machine and the agent's shell and file tools, so anything the shell runs
learning it is a privilege escalation — and the shell exists to run project code: build
scripts, test suites, npm lifecycle hooks.
"""

import os

from coworker.tools.shell import LocalExecutor, _shell_base_env


def _stdout(result) -> str:
    return str(result.get("output") or result.get("stdout") or "")


def test_sidecar_token_is_not_in_the_shell_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("COWORKER_API_TOKEN", "SIDECAR-SECRET")
    ex = LocalExecutor(cwd=str(tmp_path))
    try:
        out = _stdout(ex.run('echo "[$COWORKER_API_TOKEN]"', timeout=20))
    finally:
        ex.close()
    assert "SIDECAR-SECRET" not in out
    assert "[]" in out, f"expected an empty expansion, got {out!r}"


def test_env_listing_does_not_reveal_the_token(monkeypatch, tmp_path):
    """`echo $VAR` is not the only way to read it — `env` must not carry it either."""
    monkeypatch.setenv("COWORKER_API_TOKEN", "SIDECAR-SECRET")
    ex = LocalExecutor(cwd=str(tmp_path))
    try:
        out = _stdout(ex.run("env", timeout=20))
    finally:
        ex.close()
    assert "SIDECAR-SECRET" not in out
    assert "COWORKER_API_TOKEN" not in out


def test_the_users_own_keys_are_still_available(monkeypatch, tmp_path):
    """Scoped deliberately: the user's provider keys stay, so builds and CLIs keep working.
    Only OpenWorker's own credential is withheld."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user-key")
    ex = LocalExecutor(cwd=str(tmp_path))
    try:
        out = _stdout(ex.run('echo "[$OPENAI_API_KEY]"', timeout=20))
    finally:
        ex.close()
    assert "sk-user-key" in out


def test_base_env_drops_only_the_sidecar_vars(monkeypatch):
    monkeypatch.setenv("COWORKER_API_TOKEN", "SIDECAR-SECRET")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    env = _shell_base_env()
    assert "COWORKER_API_TOKEN" not in env
    assert "PATH" in env, "the shell still needs a usable PATH"


def test_explicit_env_overrides_still_apply(tmp_path):
    """The `env=` constructor argument is unchanged."""
    ex = LocalExecutor(cwd=str(tmp_path), env={"OW_TEST_MARKER": "present"})
    try:
        out = _stdout(ex.run('echo "[$OW_TEST_MARKER]"', timeout=20))
    finally:
        ex.close()
    assert "present" in out
