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


def test_delete(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("x", {"a": 1})
    assert store.delete("x") is True
    assert store.delete("x") is False
    assert store.get("x") is None


# -- files must be private from creation, not privatized after the fact ----------


def test_secrets_file_private_even_if_restrict_step_does_nothing(tmp_path, monkeypatch):
    """The 0600 must come from the open itself. If it only arrived via the follow-up
    _restrict_to_user, the content would sit umask-wide between write and chmod."""
    if sys.platform == "win32":
        return  # POSIX mode bits; Windows privacy is the ACL, covered above
    import coworker.secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "_restrict_to_user", lambda *a, **k: None)
    old_umask = os.umask(0)
    try:
        store = SecretStore(tmp_path / "secrets.json")
        store.put("slack:default", {"type": "token", "bot_token": "xoxb-123"})
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_write_private_text_private_even_if_restrict_step_does_nothing(
    tmp_path, monkeypatch
):
    if sys.platform == "win32":
        return  # POSIX mode bits; Windows privacy is the ACL, covered above
    import coworker.secrets as secrets_mod
    from coworker.secrets import write_private_text

    monkeypatch.setattr(secrets_mod, "_restrict_to_user", lambda *a, **k: None)
    old_umask = os.umask(0)
    try:
        out = write_private_text(tmp_path / "sidecar-8123.token", "tok\n")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_write_private_text_does_not_write_through_a_preplanted_tmp(tmp_path):
    """A symlink parked at the predictable `.tmp` name must not receive the secret.
    Path.write_text follows it — the secret lands in the symlink's target."""
    if sys.platform == "win32":
        return  # symlink creation needs privileges on Windows runners
    from coworker.secrets import write_private_text

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    target = tmp_path / "cred.token"
    (tmp_path / "cred.token.tmp").symlink_to(victim)
    out = write_private_text(target, "s3cret")
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert out.read_text(encoding="utf-8") == "s3cret"
