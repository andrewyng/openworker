"""Tests for the SecretStore (C0)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from coworker.secrets import SecretStore, _KeychainBackend, _pick_backend


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


# -- keychain backend ------------------------------------------------------------
class _FakeKeychain(_KeychainBackend):
    """The backend with the two `security` calls swapped for an in-memory item, so the
    blob encoding, import-on-first-read, and caching logic run on every platform."""

    def __init__(self, path):
        super().__init__(path)
        self.item = None  # the stored base64 blob, None = errSecItemNotFound
        self.finds = 0

    def _find(self):
        self.finds += 1
        if self.item is None:
            return SimpleNamespace(returncode=self._NOT_FOUND, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=self.item + "\n", stderr="")

    def _store_blob(self, blob):
        self.item = blob


def test_keychain_round_trip_and_status(tmp_path):
    store = SecretStore(
        tmp_path / "secrets.json", backend=_FakeKeychain(tmp_path / "secrets.json")
    )
    store.put("slack:default", {"type": "token", "bot_token": "xoxb-123"})
    assert store.get("slack:default") == {"type": "token", "bot_token": "xoxb-123"}
    assert store.get("missing") is None
    assert store.delete("slack:default") is True
    assert store.get("slack:default") is None
    # No plaintext file materializes when the keychain backend is active.
    assert not (tmp_path / "secrets.json").exists()


def test_keychain_env_refs_still_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_TOK", "from-env")
    store = SecretStore(
        tmp_path / "secrets.json", backend=_FakeKeychain(tmp_path / "secrets.json")
    )
    store.put("slack:default", {"bot_token": "${KC_TOK}"})
    assert store.get("slack:default")["bot_token"] == "from-env"


def test_keychain_imports_existing_file_once(tmp_path):
    """First read with an empty keychain seeds it from the v1 secrets.json — an existing
    setup keeps its credentials when the user flips the backend. The file is left
    untouched (non-destructive; scrubbing it is the user's call)."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("gmail:default", {"type": "oauth", "access": "tok"})

    kc = _FakeKeychain(path)
    store = SecretStore(path, backend=kc)
    assert store.get("gmail:default") == {"type": "oauth", "access": "tok"}
    assert kc.item is not None  # imported into the keychain item
    assert path.is_file()  # v1 file untouched

    # A later write goes to the keychain only; the file stays at its old contents.
    store.put("slack:default", {"bot_token": "xoxb"})
    assert "xoxb" not in path.read_text(encoding="utf-8")


def test_keychain_caches_reads(tmp_path):
    kc = _FakeKeychain(tmp_path / "secrets.json")
    store = SecretStore(tmp_path / "secrets.json", backend=kc)
    store.put("x", {"a": 1})
    store.get("x")
    store.get("x")
    store.status()
    assert kc.finds <= 1  # write-through cache: at most the initial lookup


def test_backend_selection_defaults_to_file(tmp_path, monkeypatch):
    monkeypatch.delenv("COWORKER_SECRETS_BACKEND", raising=False)
    assert type(_pick_backend(tmp_path / "s.json")).__name__ == "_FileBackend"
    monkeypatch.setenv("COWORKER_SECRETS_BACKEND", "file")
    assert type(_pick_backend(tmp_path / "s.json")).__name__ == "_FileBackend"
    # Unknown values fall back to file (the server must always boot).
    monkeypatch.setenv("COWORKER_SECRETS_BACKEND", "vault")
    assert type(_pick_backend(tmp_path / "s.json")).__name__ == "_FileBackend"


def test_backend_selection_keychain_on_macos_only(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_SECRETS_BACKEND", "keychain")
    picked = _pick_backend(tmp_path / "s.json")
    if sys.platform == "darwin":
        assert type(picked).__name__ == "_KeychainBackend"
    else:
        assert type(picked).__name__ == "_FileBackend"  # graceful fallback


@pytest.mark.skipif(sys.platform != "darwin", reason="real Keychain is macOS-only")
def test_keychain_real_round_trip(tmp_path):
    """End-to-end against the actual login Keychain (unique per-run account, cleaned up)."""
    path = tmp_path / f"secrets-{uuid.uuid4().hex}.json"
    backend = _KeychainBackend(path)
    try:
        store = SecretStore(path, backend=backend)
        store.put("slack:default", {"bot_token": "xoxb-real"})
        # A fresh backend (no cache) must read it back from the OS keychain itself.
        fresh = SecretStore(path, backend=_KeychainBackend(path))
        assert fresh.get("slack:default") == {"bot_token": "xoxb-real"}
        assert not path.exists()  # nothing plaintext on disk
    finally:
        subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s",
                _KeychainBackend.SERVICE,
                "-a",
                str(path),
            ],
            capture_output=True,
        )
