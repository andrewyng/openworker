from __future__ import annotations

import json
import os
import stat

import pytest

from coworker.browser_security.vault import (
    EncryptedBrowserProfileVault,
    InMemoryKeyProtector,
    InvalidProfileId,
    ProfileInUseError,
    VaultCorruptError,
    VaultPermissionError,
)


@pytest.fixture
def protector():
    return InMemoryKeyProtector(b"k" * 32)


@pytest.fixture
def vault(tmp_path, protector):
    return EncryptedBrowserProfileVault(tmp_path / "profiles", key_protector=protector)


def _state(token: str = "bearer-secret"):
    return {
        "cookies": [
            {
                "name": "session",
                "value": token,
                "domain": "example.com",
                "path": "/",
            }
        ],
        "origins": [
            {
                "origin": "https://example.com",
                "localStorage": [{"name": "auth", "value": token}],
            }
        ],
    }


def test_encrypted_round_trip_contains_no_plaintext(vault):
    with vault.lease("Work profile") as profile:
        profile.save(_state())
        assert profile.load() == _state()

    raw = next(vault.root.glob("*.owbv")).read_text(encoding="utf-8")
    assert "bearer-secret" not in raw
    assert "example.com" not in raw
    assert "Work profile" not in raw
    envelope = json.loads(raw)
    assert envelope["version"] == 1
    assert set(envelope) == {
        "version",
        "profile",
        "wrapped_key",
        "nonce",
        "ciphertext",
    }


def test_save_atomically_replaces_previous_state(vault):
    with vault.lease("default") as profile:
        profile.save(_state("old-token"))
        profile.save(_state("new-token"))
        assert profile.load() == _state("new-token")
    assert not list(vault.root.glob(".*.owbv.*"))


def test_user_only_permissions(vault):
    with vault.lease("default") as profile:
        profile.save(_state())
    if os.name != "nt":
        assert stat.S_IMODE(vault.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(next(vault.root.glob("*.owbv")).stat().st_mode) == 0o600
        assert stat.S_IMODE(next(vault.root.glob("*.lease")).stat().st_mode) == 0o600


def test_corruption_and_wrong_key_fail_closed(vault, tmp_path):
    with vault.lease("default") as profile:
        profile.save(_state())
    path = next(vault.root.glob("*.owbv"))
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    os.chmod(path, 0o600)
    with vault.lease("default") as profile:
        with pytest.raises(VaultCorruptError):
            profile.load()

    other = EncryptedBrowserProfileVault(
        tmp_path / "profiles", key_protector=InMemoryKeyProtector(b"x" * 32)
    )
    with other.lease("default") as profile:
        with pytest.raises(VaultCorruptError):
            profile.load()


def test_duplicate_envelope_keys_fail_closed(vault):
    with vault.lease("default") as profile:
        profile.save(_state())
    path = next(vault.root.glob("*.owbv"))
    original = path.read_text(encoding="utf-8")
    path.write_text(original[:-1] + ',"version":1}', encoding="utf-8")
    os.chmod(path, 0o600)
    with vault.lease("default") as profile:
        with pytest.raises(VaultCorruptError):
            profile.load()


def test_exclusive_profile_lease_across_vault_instances(tmp_path, protector):
    first = EncryptedBrowserProfileVault(tmp_path / "profiles", key_protector=protector)
    second = EncryptedBrowserProfileVault(tmp_path / "profiles", key_protector=protector)
    lease = first.lease("default")
    try:
        with pytest.raises(ProfileInUseError):
            second.lease("default")
    finally:
        lease.release()
    with second.lease("default"):
        pass


def test_released_lease_is_not_a_capability(vault):
    lease = vault.lease("default")
    lease.release()
    with pytest.raises(ProfileInUseError):
        lease.load()
    with pytest.raises(ProfileInUseError):
        lease.save(_state())


def test_clear_destroys_ciphertext(vault):
    with vault.lease("default") as profile:
        assert profile.load() is None
        profile.save(_state())
    assert vault.exists("default")
    assert vault.clear("default") is True
    assert vault.clear("default") is False
    assert not vault.exists("default")


@pytest.mark.parametrize("profile_id", ["", " ", "bad\nprofile", "x" * 129])
def test_invalid_profile_ids_are_rejected(vault, profile_id):
    with pytest.raises(InvalidProfileId):
        vault.lease(profile_id)


def test_profile_label_cannot_escape_vault(vault):
    with vault.lease("../../outside") as profile:
        profile.save(_state())
    assert len(list(vault.root.glob("*.owbv"))) == 1
    assert not (vault.root.parent / "outside").exists()


def test_symlink_and_broad_permissions_fail_closed(vault, tmp_path):
    with vault.lease("default") as profile:
        profile.save(_state())
    path = next(vault.root.glob("*.owbv"))
    target = tmp_path / "attacker"
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    path.symlink_to(target)
    with vault.lease("default") as profile:
        with pytest.raises(VaultPermissionError):
            profile.load()

    path.unlink()
    path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o644)
        with vault.lease("default") as profile:
            with pytest.raises(VaultPermissionError):
                profile.load()


def test_non_json_storage_state_is_rejected(vault):
    with vault.lease("default") as profile:
        with pytest.raises(ValueError):
            profile.save({"bad": float("nan")})
