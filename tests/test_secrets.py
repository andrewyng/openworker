"""Tests for the SecretStore (C0)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from coworker import secrets as secrets_module
from coworker.secrets import SecretStore, SecretStoreError


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


def test_store_is_encrypted_on_disk(tmp_path):
    """The whole point of C0-encryption: the raw file must not contain the secret in the
    clear, and must be wrapped in the versioned envelope, not a bare profile map."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("slack:default", {"type": "token", "bot_token": "xoxb-super-secret"})
    raw_text = path.read_text(encoding="utf-8")
    assert "xoxb-super-secret" not in raw_text
    payload = json.loads(raw_text)
    assert payload["__version__"] == 2
    assert "data" in payload and "slack:default" not in payload


def test_legacy_plaintext_store_migrates_in_place(tmp_path):
    """A pre-encryption secrets.json (bare `{profile: data}` JSON, no envelope) must still be
    readable, and must be transparently upgraded to the encrypted format — with the original
    bytes preserved in a `.bak` file in case the migration needs to be rolled back."""
    path = tmp_path / "secrets.json"
    legacy = {"slack:default": {"type": "token", "bot_token": "xoxb-legacy"}}
    path.write_text(json.dumps(legacy), encoding="utf-8")

    store = SecretStore(path)
    assert store.get("slack:default") == {"type": "token", "bot_token": "xoxb-legacy"}

    # Migrated in place: the file on disk is now the encrypted envelope, not the legacy shape.
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["__version__"] == 2
    assert "xoxb-legacy" not in path.read_text(encoding="utf-8")

    # Original plaintext preserved for rollback.
    backup = path.with_name(path.name + ".bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy


def test_decrypt_failure_raises_instead_of_silently_wiping(tmp_path):
    """If the wrapping key is lost/rotated and the store can't be decrypted, this must raise —
    not silently return an empty store, which would make a subsequent put() overwrite (and
    permanently lose) every existing credential."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("x", {"a": 1})

    # Simulate a lost wrapping key: forget the fake keyring entry for this store's identity.
    username = secrets_module._wrap_key_identity(path)
    if secrets_module.keyring is not None:
        secrets_module.keyring.set_password(secrets_module._KEYRING_SERVICE, username, "")

    fresh_store = SecretStore(path)  # new instance -> no cached Fernet key
    with pytest.raises(SecretStoreError):
        fresh_store.get("x")


def test_keyring_unavailable_falls_back_to_local_key_file(tmp_path, monkeypatch):
    """Headless environments with no OS keychain backend (e.g. Linux CI with no Secret Service)
    must still encrypt at rest, via a local 0600 key file instead of the keychain."""
    monkeypatch.setattr(secrets_module, "keyring", None)
    path = tmp_path / "secrets.json"

    with pytest.warns(RuntimeWarning, match="no OS keychain backend available"):
        SecretStore(path).put("slack:default", {"bot_token": "xoxb-fallback"})

    assert "xoxb-fallback" not in path.read_text(encoding="utf-8")
    key_path = path.parent / ".secrets.key"
    assert key_path.is_file()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600

    # A second store instance reusing the same file-backed key can still decrypt.
    assert SecretStore(path).get("slack:default") == {"bot_token": "xoxb-fallback"}
