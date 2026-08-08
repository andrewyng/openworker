"""Unit tests for Matrix E2EE bootstrap (recovery key, stale keys, SSSS decrypt)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

from coworker.connectors.matrix_crypto_bootstrap import (
    MatrixCryptoBootstrapError,
    _B58,
    _SSSS_ZERO_SALT,
    _decrypt_secret,
    _hkdf_keys,
    _unpadded_b64,
    _verify_storage_key,
    _xor_bytes,
    bootstrap_cross_signing,
    detect_stale_device_keys,
    parse_recovery_key,
    prepare_matrix_e2ee,
)


def _encode_recovery_key(key: bytes) -> str:
    assert len(key) == 32
    payload = b"\x8b\x01" + key
    parity = _xor_bytes(payload) & 0xFF
    data = payload + bytes([parity])
    pad = 0
    for b in data:
        if b == 0:
            pad += 1
        else:
            break
    num = int.from_bytes(data, "big")
    encoded = ""
    while num > 0:
        num, rem = divmod(num, 58)
        encoded = _B58[rem] + encoded
    return _B58[0] * pad + encoded


def _encrypt_secret(storage_key: bytes, secret_name: str, plaintext: bytes) -> dict:
    aes_key, mac_key = _hkdf_keys(storage_key, secret_name.encode("utf-8"))
    iv = os.urandom(16)
    iv_arr = bytearray(iv)
    iv_arr[8] &= 0x7F
    cipher = AES.new(aes_key, AES.MODE_CTR, nonce=b"", initial_value=bytes(iv_arr))
    ct = cipher.encrypt(plaintext)
    mac = hmac.new(mac_key, ct, hashlib.sha256).digest()
    return {
        "iv": _unpadded_b64(iv),
        "ciphertext": _unpadded_b64(ct),
        "mac": _unpadded_b64(mac),
    }


def _storage_key_self_check(storage_key: bytes) -> dict:
    aes_key, mac_key = _hkdf_keys(storage_key, b"")
    iv = os.urandom(16)
    iv_arr = bytearray(iv)
    iv_arr[8] &= 0x7F
    cipher = AES.new(aes_key, AES.MODE_CTR, nonce=b"", initial_value=bytes(iv_arr))
    ct = cipher.encrypt(b"\x00" * 32)
    mac = hmac.new(mac_key, ct, hashlib.sha256).digest()
    return {"iv": _unpadded_b64(iv), "mac": _unpadded_b64(mac)}


def test_parse_recovery_key_hex():
    key = os.urandom(32)
    assert parse_recovery_key(key.hex()) == key
    assert parse_recovery_key("0x" + key.hex()) == key


def test_parse_recovery_key_roundtrip():
    key = os.urandom(32)
    encoded = _encode_recovery_key(key)
    assert parse_recovery_key(encoded) == key
    assert parse_recovery_key("  " + encoded + " ") == key


def test_parse_recovery_key_invalid():
    with pytest.raises(MatrixCryptoBootstrapError, match="empty"):
        parse_recovery_key("")
    with pytest.raises(MatrixCryptoBootstrapError, match="invalid recovery key encoding"):
        parse_recovery_key("!!!")


def test_decrypt_secret_roundtrip():
    storage_key = os.urandom(32)
    name = "m.cross_signing.self_signing"
    plain = b'{"private_key":"abc"}'
    blob = _encrypt_secret(storage_key, name, plain)
    assert _decrypt_secret(storage_key, name, blob) == plain


def test_verify_storage_key():
    storage_key = os.urandom(32)
    desc = _storage_key_self_check(storage_key)
    _verify_storage_key(storage_key, desc)
    with pytest.raises(MatrixCryptoBootstrapError, match="does not match"):
        _verify_storage_key(os.urandom(32), desc)


def test_detect_stale_device_keys_mismatch():
    device_id = "DEV123"
    user_id = "@bot:example.com"
    remote = {
        "keys": {
            f"curve25519:{device_id}": "remote_curve",
            f"ed25519:{device_id}": "remote_ed",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/keys/query")
        return httpx.Response(200, json={"device_keys": {user_id: {device_id: remote}}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with patch(
            "coworker.connectors.matrix_crypto_bootstrap.httpx.post",
            side_effect=lambda *a, **kw: client.post(*a, **kw),
        ):
            err = detect_stale_device_keys(
                base_url="https://matrix.example.com",
                token="tok",
                user_id=user_id,
                device_id=device_id,
                local_curve25519="local_curve",
                local_ed25519="local_ed",
            )
    assert err and "stale keys" in err


def test_detect_stale_device_keys_ok():
    device_id = "DEV123"
    user_id = "@bot:example.com"
    curve = "same_curve"
    ed = "same_ed"
    remote = {
        "keys": {
            f"curve25519:{device_id}": curve,
            f"ed25519:{device_id}": ed,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"device_keys": {user_id: {device_id: remote}}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with patch(
            "coworker.connectors.matrix_crypto_bootstrap.httpx.post",
            side_effect=lambda *a, **kw: client.post(*a, **kw),
        ):
            assert (
                detect_stale_device_keys(
                    base_url="https://matrix.example.com",
                    token="tok",
                    user_id=user_id,
                    device_id=device_id,
                    local_curve25519=curve,
                    local_ed25519=ed,
                )
                is None
            )


def test_bootstrap_cross_signing_uploads_signature():
    storage_key = os.urandom(32)
    recovery_key = _encode_recovery_key(storage_key)
    key_id = "abc123"
    user_id = "@bot:example.com"
    device_id = "OWDEVICE"
    seed = os.urandom(32)
    secret_json = json.dumps({"private_key": _unpadded_b64(seed)}).encode()
    enc_blob = _encrypt_secret(storage_key, "m.cross_signing.self_signing", secret_json)
    key_desc = _storage_key_self_check(storage_key)

    device_keys = {
        "algorithms": ["m.olm.v1.curve25519-aes-sha2", "m.megolm.v1.aes-sha2"],
        "device_id": device_id,
        "user_id": user_id,
        "keys": {
            f"curve25519:{device_id}": "curve",
            f"ed25519:{device_id}": "ed",
        },
        "signatures": {user_id: {f"ed25519:{device_id}": "selfsig"}},
    }

    uploads: list[dict] = []

    def _resp(status: int, *, json=None, method: str = "GET", url: str = "https://matrix.example.com/x"):
        return httpx.Response(
            status,
            json=json,
            request=httpx.Request(method, url),
        )

    def get_handler(url: str, **kwargs):
        req_url = str(url)
        if "default_key" in req_url:
            return _resp(200, json={"key": key_id}, url=req_url)
        if f"m.secret_storage.key.{key_id}" in req_url:
            return _resp(200, json=key_desc, url=req_url)
        if "self_signing" in req_url:
            return _resp(200, json={"encrypted": {key_id: enc_blob}}, url=req_url)
        return _resp(404, url=req_url)

    def post_handler(url: str, **kwargs):
        assert url.endswith("/keys/signatures/upload")
        uploads.append(kwargs.get("json") or {})
        return _resp(200, json={}, method="POST", url=url)

    with patch(
        "coworker.connectors.matrix_crypto_bootstrap.httpx.get", side_effect=get_handler
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap.httpx.post", side_effect=post_handler
    ):
        bootstrap_cross_signing(
            base_url="https://matrix.example.com",
            token="tok",
            user_id=user_id,
            device_id=device_id,
            recovery_key=recovery_key,
            device_keys=device_keys,
        )

    assert len(uploads) == 1
    signed = uploads[0][user_id][device_id]
    assert f"ed25519:{device_id}" in signed["signatures"][user_id]
    cross_sigs = [
        k for k in signed["signatures"][user_id] if k != f"ed25519:{device_id}"
    ]
    assert len(cross_sigs) == 1


@pytest.mark.asyncio
async def test_prepare_matrix_e2ee_requires_recovery_key_when_ssss():
    client = MagicMock()
    client.olm = MagicMock()
    client.olm.account.identity_keys = {"curve25519": "c", "ed25519": "e"}
    client.user_id = "@bot:example.com"
    client.device_id = "DEV"
    client.should_upload_keys = False
    settings = MagicMock(
        e2ee_mode="required",
        recovery_key=None,
        homeserver_url="https://matrix.example.com",
        access_token="tok",
    )
    with patch(
        "coworker.connectors.matrix_crypto_bootstrap.detect_stale_device_keys",
        return_value=None,
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap._get_account_data",
        return_value={"key": "abc"},
    ):
        with pytest.raises(MatrixCryptoBootstrapError, match="recovery_key is required"):
            await prepare_matrix_e2ee(client, settings)


@pytest.mark.asyncio
async def test_prepare_matrix_e2ee_skips_cross_signing_without_ssss():
    client = MagicMock()
    client.olm = MagicMock()
    client.olm.account.identity_keys = {"curve25519": "c", "ed25519": "e"}
    client.user_id = "@bot:example.com"
    client.device_id = "DEV"
    client.should_upload_keys = False
    settings = MagicMock(
        e2ee_mode="required",
        recovery_key=None,
        homeserver_url="https://matrix.example.com",
        access_token="tok",
    )
    bootstrap = AsyncMock()
    with patch(
        "coworker.connectors.matrix_crypto_bootstrap.detect_stale_device_keys",
        return_value=None,
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap._get_account_data",
        return_value=None,
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap.bootstrap_cross_signing",
        bootstrap,
    ):
        await prepare_matrix_e2ee(client, settings)
    bootstrap.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_matrix_e2ee_skips_when_disabled():
    client = MagicMock()
    settings = MagicMock(e2ee_mode="off", recovery_key=None)
    await prepare_matrix_e2ee(client, settings)
    client.olm.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_matrix_e2ee_stale_keys_fail_closed():
    client = MagicMock()
    client.olm.account.identity_keys = {"curve25519": "local", "ed25519": "local_ed"}
    client.user_id = "@bot:example.com"
    client.device_id = "DEV"
    client.should_upload_keys = False
    settings = MagicMock(
        e2ee_mode="required",
        recovery_key="EsT fake",
        homeserver_url="https://matrix.example.com",
        access_token="tok",
    )
    with patch(
        "coworker.connectors.matrix_crypto_bootstrap.detect_stale_device_keys",
        return_value="stale keys on server",
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap.parse_recovery_key",
        return_value=b"\x00" * 32,
    ):
        with pytest.raises(MatrixCryptoBootstrapError, match="stale keys"):
            await prepare_matrix_e2ee(client, settings)


@pytest.mark.asyncio
async def test_prepare_matrix_e2ee_calls_bootstrap():
    client = MagicMock()
    client.olm.account.identity_keys = {"curve25519": "c", "ed25519": "e"}
    client.olm._algorithms = ["m.olm.v1.curve25519-aes-sha2"]
    client.olm.sign_json.return_value = "sig"
    client.user_id = "@bot:example.com"
    client.device_id = "DEV"
    client.should_upload_keys = False
    settings = MagicMock(
        e2ee_mode="required",
        recovery_key="EsT fake",
        homeserver_url="https://matrix.example.com",
        access_token="tok",
    )
    bootstrap = MagicMock()
    with patch(
        "coworker.connectors.matrix_crypto_bootstrap.detect_stale_device_keys",
        return_value=None,
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap._get_account_data",
        return_value={"key": "abc"},
    ), patch(
        "coworker.connectors.matrix_crypto_bootstrap.bootstrap_cross_signing",
        bootstrap,
    ):
        await prepare_matrix_e2ee(client, settings)
    bootstrap.assert_called_once()
