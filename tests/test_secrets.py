"""Tests for the SecretStore (C0)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

from coworker.secrets import SecretStore


def test_put_get_round_trip(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("slack:default", {"type": "token", "bot_token": "xoxb-123"})
    assert store.get("slack:default") == {"type": "token", "bot_token": "xoxb-123"}
    assert store.get("missing") is None


def test_env_ref_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOK", "from-env")
    store = SecretStore(tmp_path / "secrets.json")
    store.put("slack:default", {"type": "token", "bot_token": "${MY_TOK}"})
    assert store.get("slack:default")["bot_token"] == "from-env"


def test_dotenv_ref_resolution(tmp_path):
    (tmp_path / ".env").write_text('DOCS_TOKEN = "shhh"\n', encoding="utf-8")
    store = SecretStore(tmp_path / "secrets.json")
    store.put("docs:default", {"headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}})
    assert store.get("docs:default")["headers"]["Authorization"] == "Bearer shhh"


def test_unresolved_ref_left_intact(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("x", {"v": "${NOPE_NOT_SET}"})
    assert store.get("x")["v"] == "${NOPE_NOT_SET}"


def test_status_hides_values(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put(
        "gmail:default",
        {
            "type": "oauth",
            "access": "secret",
            "account_id": "me@x.com",
            "expires": time.time() - 10,
        },
    )
    store.put("slack:default", {"type": "token", "bot_token": "xoxb"})
    status = {row["profile"]: row for row in store.status()}
    assert status["gmail:default"]["type"] == "oauth"
    assert status["gmail:default"]["account"] == "me@x.com"
    assert status["gmail:default"]["expired"] is True
    assert status["slack:default"]["expired"] is False
    # No secret material anywhere in the status payload.
    blob = str(store.status())
    assert "secret" not in blob and "xoxb" not in blob


def test_secrets_file_is_restricted(tmp_path):
    """The secrets file must be restricted to the current user. POSIX expresses this as mode
    0600; Windows has no such bits, so we assert the ACL instead (inheritance stripped, only
    the current user granted)."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("x", {"a": 1})
    if sys.platform == "win32":
        out = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True
        ).stdout
        user = os.environ.get("USERNAME", "")
        assert user and user in out  # current user is granted
        # Inherited broad principals must be gone after /inheritance:r.
        assert "NT AUTHORITY\\SYSTEM" not in out
        assert "BUILTIN\\Administrators" not in out
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_secrets_tmp_file_never_world_readable(tmp_path, monkeypatch):
    """Regression test: the .tmp file used during an atomic write must be created already
    restricted (0600), not written-then-chmod'd. A separate chmod-after-write leaves a window
    where the plaintext secrets file has the process's default (often world/group-readable)
    umask permissions. This asserts the file is 0600 the instant it's created, by checking its
    mode inside a wrapped `os.open` before any subsequent chmod could run."""
    if sys.platform == "win32":
        return  # POSIX-only: Windows has no creation-time mode bits, see _restrict_to_user

    observed_modes = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = (
            real_open(path, flags, mode, dir_fd=dir_fd)
            if dir_fd is not None
            else real_open(path, flags, mode)
        )
        if str(path).endswith(".tmp"):
            observed_modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fd

    monkeypatch.setattr(os, "open", spy_open)

    path = tmp_path / "secrets.json"
    SecretStore(path).put("x", {"a": 1})

    assert observed_modes, "expected the .tmp file to be created via os.open"
    assert all(m == 0o600 for m in observed_modes), (
        f"tmp file was created at {[oct(m) for m in observed_modes]}, "
        "not 0600 — it must never exist at default umask permissions"
    )


def test_delete(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("x", {"a": 1})
    assert store.delete("x") is True
    assert store.delete("x") is False
    assert store.get("x") is None