"""Matrix E2EE bootstrap — recovery key (SSSS) + stale device-key detection.

matrix-nio has no cross-signing / secret-storage support; this module implements the
subset needed for Element-style bot accounts on self-hosted Synapse:
- Parse Element recovery keys (base58)
- Decrypt cross-signing secrets from account data (m.secret_storage.v1.aes-hmac-sha2)
- Sign the current device with the self-signing key
- Detect local/server identity mismatch (stale OTM / deleted crypto store)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Protocol.KDF import HKDF
from Crypto.Signature import eddsa

logger = logging.getLogger("coworker.connectors.matrix")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SSSS_ZERO_SALT = b"\x00" * 32
_CROSS_SIGNING_SELF = "m.cross_signing.self_signing"


class MatrixCryptoBootstrapError(Exception):
    """E2EE bootstrap failed — connect must fail closed."""


def _unpadded_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(data: str) -> bytes:
    return base64.b64decode(data + "=" * (-len(data) % 4))


def parse_recovery_key(raw: str) -> bytes:
    """Element recovery / security key → 32-byte secret storage key.

    Accepts Element base58 recovery keys or a raw 32-byte hex string (64 hex chars).
    """
    cleaned = "".join(raw.split())
    if not cleaned:
        raise MatrixCryptoBootstrapError("recovery key is empty")
    hex_candidate = cleaned.removeprefix("0x")
    if len(hex_candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in hex_candidate):
        try:
            key = bytes.fromhex(hex_candidate)
        except ValueError as exc:
            raise MatrixCryptoBootstrapError("invalid recovery key hex") from exc
        if len(key) != 32:
            raise MatrixCryptoBootstrapError("recovery key hex must decode to 32 bytes")
        return key
    num = 0
    for ch in cleaned:
        try:
            num = num * 58 + _B58.index(ch)
        except ValueError as exc:
            raise MatrixCryptoBootstrapError("invalid recovery key encoding") from exc
    # Preserve leading zero bytes.
    full = num.to_bytes((num.bit_length() + 7) // 8 or 1, "big")
    pad = len(cleaned) - len(cleaned.lstrip(_B58[0]))
    decoded = b"\x00" * pad + full
    if len(decoded) != 35:
        raise MatrixCryptoBootstrapError(
            f"recovery key decoded to {len(decoded)} bytes (expected 35)"
        )
    if decoded[:2] != b"\x8b\x01":
        raise MatrixCryptoBootstrapError("recovery key has invalid prefix")
    key = decoded[2:34]
    parity = decoded[34]
    if (parity ^ _xor_bytes(decoded[:34])) & 0xFF:
        raise MatrixCryptoBootstrapError("recovery key parity check failed")
    return key


def _xor_bytes(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


def _hkdf_keys(storage_key: bytes, info: bytes) -> tuple[bytes, bytes]:
    out = HKDF(
        storage_key,
        64,
        _SSSS_ZERO_SALT,
        SHA256,
        context=info,
    )
    return out[:32], out[32:]


def _verify_storage_key(storage_key: bytes, key_desc: dict) -> None:
    """Optional iv/mac self-check on m.secret_storage.key.* account data."""
    iv_b64 = key_desc.get("iv")
    mac_b64 = key_desc.get("mac")
    if not iv_b64 or not mac_b64:
        return
    aes_key, mac_key = _hkdf_keys(storage_key, b"")
    iv = _b64_decode(iv_b64)
    if len(iv) != 16:
        raise MatrixCryptoBootstrapError("invalid secret storage key iv")
    iv = bytearray(iv)
    iv[8] &= 0x7F
    cipher = AES.new(aes_key, AES.MODE_CTR, nonce=b"", initial_value=iv)
    ct = cipher.encrypt(b"\x00" * 32)
    expected = hmac.new(mac_key, ct, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64_decode(mac_b64), expected):
        raise MatrixCryptoBootstrapError("recovery key does not match this account")


def _decrypt_secret(
    storage_key: bytes, secret_name: str, blob: dict
) -> bytes:
    aes_key, mac_key = _hkdf_keys(storage_key, secret_name.encode("utf-8"))
    iv = _b64_decode(str(blob["iv"]))
    if len(iv) != 16:
        raise MatrixCryptoBootstrapError(f"invalid iv for secret {secret_name}")
    iv = bytearray(iv)
    iv[8] &= 0x7F
    ct = _b64_decode(str(blob["ciphertext"]))
    expected_mac = hmac.new(mac_key, ct, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64_decode(str(blob["mac"])), expected_mac):
        raise MatrixCryptoBootstrapError(f"MAC mismatch for secret {secret_name}")
    cipher = AES.new(aes_key, AES.MODE_CTR, nonce=b"", initial_value=bytes(iv))
    return cipher.decrypt(ct)


def _sign_json_ed25519(seed: bytes, payload: dict) -> str:
    from nio.api import Api

    unsigned = {k: v for k, v in payload.items() if k not in ("signatures", "unsigned")}
    message = Api.to_canonical_json(unsigned).encode("utf-8")
    key = ECC.construct(curve="Ed25519", seed=seed)
    return _unpadded_b64(eddsa.new(key, "rfc8032").sign(message))


def _public_key_b64(seed: bytes) -> str:
    raw = ECC.construct(curve="Ed25519", seed=seed).public_key().export_key(format="raw")
    return _unpadded_b64(raw)


def _get_account_data(
    base_url: str, token: str, user_id: str, event_type: str
) -> Optional[dict]:
    from urllib.parse import quote

    url = (
        f"{base_url.rstrip('/')}/_matrix/client/v3/user/"
        f"{quote(user_id, safe='')}/account_data/{quote(event_type, safe='')}"
    )
    resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _upload_signatures(
    base_url: str, token: str, body: dict
) -> None:
    url = f"{base_url.rstrip('/')}/_matrix/client/v3/keys/signatures/upload"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30,
    )
    if resp.status_code >= 400:
        detail = resp.text[:200]
        raise MatrixCryptoBootstrapError(
            f"signatures/upload failed ({resp.status_code}): {detail}"
        )


def _query_own_device_keys(
    base_url: str, token: str, user_id: str, device_id: str
) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}/_matrix/client/v3/keys/query"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"device_keys": {user_id: [device_id]}},
        timeout=30,
    )
    if resp.status_code >= 400:
        return None
    data = resp.json()
    dev = (data.get("device_keys") or {}).get(user_id, {}).get(device_id)
    return dev if isinstance(dev, dict) else None


def detect_stale_device_keys(
    *,
    base_url: str,
    token: str,
    user_id: str,
    device_id: str,
    local_curve25519: str,
    local_ed25519: str,
) -> Optional[str]:
    """Return an actionable error if the homeserver has different identity keys."""
    remote = _query_own_device_keys(base_url, token, user_id, device_id)
    if not remote:
        return None
    keys = remote.get("keys") or {}
    remote_curve = keys.get(f"curve25519:{device_id}")
    remote_ed = keys.get(f"ed25519:{device_id}")
    if not remote_curve and not remote_ed:
        return None
    if remote_curve and remote_curve != local_curve25519:
        return (
            f"device {device_id} has stale keys on the server (identity key mismatch). "
            "Delete the local crypto store or generate a new access token (fresh device id)."
        )
    if remote_ed and remote_ed != local_ed25519:
        return (
            f"device {device_id} has stale signing keys on the server. "
            "Generate a new access token or delete the device via Synapse admin API."
        )
    return None


def bootstrap_cross_signing(
    *,
    base_url: str,
    token: str,
    user_id: str,
    device_id: str,
    recovery_key: str,
    device_keys: dict,
) -> None:
    """Import self-signing key from SSSS and cross-sign the current device."""
    storage_key = parse_recovery_key(recovery_key)

    default = _get_account_data(base_url, token, user_id, "m.secret_storage.default_key")
    if not default or not default.get("key"):
        raise MatrixCryptoBootstrapError(
            "no default secret storage key on account — set up cross-signing in Element first"
        )
    key_id = str(default["key"])
    key_desc = _get_account_data(
        base_url, token, user_id, f"m.secret_storage.key.{key_id}"
    )
    if not key_desc:
        raise MatrixCryptoBootstrapError(f"secret storage key {key_id!r} not found")
    _verify_storage_key(storage_key, key_desc)

    secret_event = _get_account_data(
        base_url, token, user_id, "m.cross_signing.self_signing"
    )
    if not secret_event or "encrypted" not in secret_event:
        raise MatrixCryptoBootstrapError(
            "m.cross_signing.self_signing not in account data — enable secure backup in Element"
        )
    enc = (secret_event.get("encrypted") or {}).get(key_id)
    if not enc:
        raise MatrixCryptoBootstrapError(
            "self_signing secret not encrypted with the default storage key"
        )
    plain = _decrypt_secret(storage_key, _CROSS_SIGNING_SELF, enc)
    try:
        parsed = json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise MatrixCryptoBootstrapError("self_signing secret is not valid JSON") from exc
    seed_b64 = parsed.get("private_key") or parsed.get("key")
    if not seed_b64:
        raise MatrixCryptoBootstrapError("self_signing secret missing private_key")
    seed = _b64_decode(str(seed_b64))
    if len(seed) != 32:
        raise MatrixCryptoBootstrapError("self_signing private key must be 32 bytes")

    unsigned = {
        k: v for k, v in device_keys.items() if k not in ("signatures", "unsigned")
    }
    sig = _sign_json_ed25519(seed, unsigned)
    pub = _public_key_b64(seed)
    signed = dict(device_keys)
    signatures = dict(signed.get("signatures") or {})
    user_sigs = dict(signatures.get(user_id) or {})
    user_sigs[f"ed25519:{pub}"] = sig
    signatures[user_id] = user_sigs
    signed["signatures"] = signatures

    _upload_signatures(
        base_url,
        token,
        {user_id: {device_id: signed}},
    )
    logger.info("matrix cross-signing: signed device %s", device_id)


async def prepare_matrix_e2ee(client: Any, settings: Any) -> None:
    """Run stale-key check, keys upload, and optional cross-signing bootstrap."""
    if settings.e2ee_mode != "required":
        return
    if client.olm is None:
        raise MatrixCryptoBootstrapError("encryption store not loaded")

    user_id = client.user_id
    device_id = client.device_id or getattr(client.olm, "device_id", None)
    if not user_id or not device_id:
        raise MatrixCryptoBootstrapError("whoami did not return user_id/device_id")

    local_curve = client.olm.account.identity_keys["curve25519"]
    local_ed = client.olm.account.identity_keys["ed25519"]
    stale = detect_stale_device_keys(
        base_url=settings.homeserver_url,
        token=settings.access_token,
        user_id=user_id,
        device_id=device_id,
        local_curve25519=local_curve,
        local_ed25519=local_ed,
    )
    if stale:
        raise MatrixCryptoBootstrapError(stale)

    from nio.responses import KeysUploadError

    if client.should_upload_keys:
        resp = await client.keys_upload()
        if isinstance(resp, KeysUploadError):
            msg = getattr(resp, "message", None) or str(resp)
            if "identity" in msg.lower() or "one.time" in msg.lower():
                raise MatrixCryptoBootstrapError(
                    f"keys/upload failed (stale device keys?): {msg}. "
                    "Generate a new access token or delete the device on Synapse."
                )
            raise MatrixCryptoBootstrapError(f"keys/upload failed: {msg}")

    import asyncio

    default = await asyncio.to_thread(
        _get_account_data,
        settings.homeserver_url,
        settings.access_token,
        user_id,
        "m.secret_storage.default_key",
    )
    if not default or not default.get("key"):
        logger.info(
            "matrix: account has no secret storage — skipping cross-signing bootstrap"
        )
        return

    if not settings.recovery_key:
        raise MatrixCryptoBootstrapError(
            "recovery_key is required — this account has cross-signing enabled. "
            "Export it from Element (Settings → Security & Privacy → Recovery key)."
        )

    device_keys = _local_device_keys(client.olm, user_id, device_id)
    await asyncio.to_thread(
        bootstrap_cross_signing,
        base_url=settings.homeserver_url,
        token=settings.access_token,
        user_id=user_id,
        device_id=device_id,
        recovery_key=settings.recovery_key,
        device_keys=device_keys,
    )


def _local_device_keys(olm: Any, user_id: str, device_id: str) -> dict:
    base = {
        "algorithms": olm._algorithms,
        "device_id": device_id,
        "user_id": user_id,
        "keys": {
            f"curve25519:{device_id}": olm.account.identity_keys["curve25519"],
            f"ed25519:{device_id}": olm.account.identity_keys["ed25519"],
        },
    }
    sig = olm.sign_json(base)
    base["signatures"] = {user_id: {f"ed25519:{device_id}": sig}}
    return base
