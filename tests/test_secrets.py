"""Tests for the SecretStore (C0)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

import pytest

from coworker.secrets import SecretStore, SecretStoreCorrupt


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


def test_temp_file_is_never_world_readable_mid_write(tmp_path, monkeypatch):
    """The plaintext must never touch disk at the process umask. write_text created the temp
    at 0644 and narrowed it only afterwards, leaving a readable window.

    The spy fires on the descriptor before any byte is written, so this fails two ways: the
    write path stops going through an fd we can inspect, or the fd is created permissively.
    Deliberately does NOT patch os.umask — nothing in secrets.py calls it, so a patch there
    would read as though the permissive-umask case were covered when it is not. The real
    guard is that mkstemp's mode does not depend on the umask at all.
    """
    if sys.platform == "win32":
        return  # mode bits are meaningless here; the ACL is asserted above
    seen: list[int] = []
    real_fdopen = os.fdopen

    def spy(fd, *args, **kwargs):
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", spy)
    SecretStore(tmp_path / "secrets.json").put("slack:default", {"bot_token": "xoxb-1"})
    assert seen and all(mode == 0o600 for mode in seen)


def test_no_stray_temp_files_left_behind(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("a", {"v": 1})
    store.put("b", {"v": 2})
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]


def test_no_temp_file_left_behind_when_the_write_fails(tmp_path, monkeypatch):
    """The success path cleans up via os.replace; the failure path needs the handler. A
    store directory slowly filling with world-readable `.tmp` fragments is the same
    disclosure the mode fix closes, arriving by a different route."""
    store = SecretStore(tmp_path / "secrets.json")
    store.put("a", {"v": 1})
    real_replace = os.replace

    def boom(src, dst, *args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.put("b", {"v": 2})
    monkeypatch.setattr(os, "replace", real_replace)
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]


def test_each_write_uses_a_distinct_temp_path(tmp_path, monkeypatch):
    """The fixed `<name>.tmp` was shared by every process pointed at the same store — the
    server and the openworker-connectors CLI, say — so two concurrent writes could
    interleave into one temp and rename the mixture into place.

    Observes the paths actually handed to os.replace rather than spying on mkstemp, so it
    holds for any implementation that reaches uniqueness some other way.
    """
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        seen.append(str(src))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    store = SecretStore(tmp_path / "secrets.json")
    for key in ("a", "b", "c"):
        store.put(key, {"v": key})
    assert len(seen) == 3
    assert len(set(seen)) == 3, f"temp path reused across writes: {seen}"


def test_store_is_fsynced_before_the_rename(tmp_path, monkeypatch):
    """Without an fsync a crash between write and rename can promote a truncated temp, and
    that truncated file is exactly what makes _read unparseable.

    A mechanism check, not a durability proof — it confirms fsync is called on the store's
    own descriptor before the rename, which is as far as a test can go without a crash
    harness. See the coverage-limits note at the bottom of this module.
    """
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        os, "replace", lambda s, d, *a, **k: (order.append("replace"), real_replace(s, d))[1]
    )
    SecretStore(tmp_path / "secrets.json").put("a", {"v": 1})
    assert order == ["fsync", "replace"]


def test_store_directory_is_created_and_restricted(tmp_path):
    """The store's parent is created on demand — the first write on a fresh machine lands in
    a directory that does not exist yet — and is restricted to the user, since a 0755 parent
    lets anyone list which connectors are configured even when the file itself is 0600.
    """
    path = tmp_path / "state" / "coworker" / "secrets.json"
    store = SecretStore(path)
    store.put("slack:default", {"bot_token": "xoxb-1"})
    assert store.get("slack:default") == {"bot_token": "xoxb-1"}
    if sys.platform != "win32":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_store_is_not_silently_overwritten(tmp_path):
    """A truncated secrets.json used to parse as {}, so the next put() wrote a one-key store
    and discarded every other connector credential."""
    path = tmp_path / "secrets.json"
    store = SecretStore(path)
    store.put("slack:default", {"bot_token": "xoxb-1"})
    store.put("github:default", {"token": "ghp-1"})

    path.write_text('{"slack:default": {"bot_to', encoding="utf-8")  # truncated write

    with pytest.raises(SecretStoreCorrupt):
        store.put("notion:default", {"token": "secret-1"})
    with pytest.raises(SecretStoreCorrupt):
        store.delete("slack:default")
    assert path.read_text(encoding="utf-8") == '{"slack:default": {"bot_to'


def test_non_object_store_is_not_silently_overwritten(tmp_path):
    """Valid JSON that is not an object — `[]`, `null`, a bare string — parses fine and then
    fails the isinstance check. It reaches the same discard-every-credential outcome as a
    truncated file, so the strict path has to refuse it too."""
    for payload in ("[]", "null", '"a string"'):
        path = tmp_path / f"secrets-{len(payload)}.json"
        path.write_text(payload, encoding="utf-8")
        store = SecretStore(path)
        with pytest.raises(SecretStoreCorrupt):
            store.put("notion:default", {"token": "secret-1"})
        assert path.read_text(encoding="utf-8") == payload


def test_reads_still_degrade_gracefully_on_a_corrupt_store(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("{not json", encoding="utf-8")
    store = SecretStore(path)
    assert store.get("anything") is None
    assert store.status() == []


# -- what these checks do NOT cover -------------------------------------------
#
# Stated rather than left implicit: a green run is silent about the thing it did not look
# at in exactly the tone it uses for the thing it approved.
#
# * **Durability across a real crash.** test_store_is_fsynced_before_the_rename asserts the
#   call order, not that the bytes survive power loss. Proving that needs a crash harness
#   (or a filesystem fault injector); nothing here exercises it.
# * **Genuine write concurrency.** test_each_write_uses_a_distinct_temp_path establishes
#   that sequential writes do not collide on a name. Two processes racing on the same store
#   are not simulated, and the cross-process case has no lock — self._lock is per-instance.
# * **Windows ACLs.** The mode assertions are skipped on win32; _restrict_to_user's ACL
#   behaviour there is covered only by the pre-existing platform test.
# * **Partial-line truncation that still parses.** A store truncated at a point that happens
#   to yield valid JSON is indistinguishable from a smaller store, and no checksum exists to
#   tell them apart.
