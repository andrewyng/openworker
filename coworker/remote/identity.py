"""Machine identity: an ed25519 keypair generated on the box before it ever dials.

The enrollment token's ONLY job is to bind a new public key to the controller
once; every reconnect proves possession of the private key via challenge-
response. Lost state dir = lost identity = re-enroll (by design — there is no
key recovery, and the controller's clone trap refuses a second concurrent
connection per public key).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_FILENAME = "machine.key"
# The SEALING key (x25519) is separate from the signing key on purpose: one key,
# one job. Its public half rides the enrollment hello and is pinned beside the
# signing key — wallet deploys are encrypted to it, so a future relay forwards
# ciphertext it cannot read (remote-home-design.md §Keys wallet).
SEAL_KEY_FILENAME = "machine.seal.key"

_SEAL_INFO = b"openworker-seal-v1"
# Domain separator for the seal-key attestation: without it, an attacker who can
# get the box to sign arbitrary bytes could forge an attestation from any
# signature oracle. The identity key signs ONLY nonces and this tagged message.
_SEAL_ATTEST_PREFIX = b"openworker-seal-attest-v1"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


@dataclass
class MachineIdentity:
    private_key: Ed25519PrivateKey
    seal_key: X25519PrivateKey

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64(raw)

    @property
    def fingerprint(self) -> str:
        """Short human-checkable form shown in `status` and the Machines panel."""
        import hashlib

        raw = _unb64(self.public_key_b64)
        return hashlib.sha256(raw).hexdigest()[:16]

    def sign_b64(self, data: bytes) -> str:
        return _b64(self.private_key.sign(data))

    @property
    def seal_fingerprint(self) -> str:
        """Fingerprint of the SEALING key — what the dashboard's Keys card
        shows; `status` prints it so the user can compare the two."""
        import hashlib

        raw = _unb64(self.seal_public_key_b64)
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def seal_public_key_b64(self) -> str:
        raw = self.seal_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64(raw)

    def attest_seal_key_b64(self) -> str:
        """Sign the sealing public key with the identity key. The hello carries
        this beside seal_pubkey; the acceptor refuses to pin without it —
        otherwise a middlebox could substitute its own sealing key (the
        challenge only ever proves the identity key)."""
        return self.sign_b64(_SEAL_ATTEST_PREFIX + _unb64(self.seal_public_key_b64))

    def unseal_b64(self, blob_b64: str) -> bytes:
        """Open a payload sealed to this machine's sealing key."""
        blob = _unb64(blob_b64)
        if len(blob) < 32 + 16:
            raise ValueError("sealed blob too short")
        ephemeral_pub = blob[:32]
        ciphertext = blob[32:]
        shared = self.seal_key.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub))
        key = _seal_kdf(shared, ephemeral_pub, _unb64(self.seal_public_key_b64))
        try:
            return ChaCha20Poly1305(key).decrypt(b"\x00" * 12, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("sealed blob failed to authenticate") from exc


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_bytes(data)


def load_or_create(state_dir: Path) -> MachineIdentity:
    """Load the box's keypairs (signing + sealing), creating them (0600) on
    first use. A pre-sealing state dir grows the sealing key in place."""
    path = Path(state_dir) / KEY_FILENAME
    if path.exists():
        raw = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(raw, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an ed25519 private key")
        key = raw
    else:
        key = Ed25519PrivateKey.generate()
        _write_private(
            path,
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
    seal_path = Path(state_dir) / SEAL_KEY_FILENAME
    if seal_path.exists():
        raw = serialization.load_pem_private_key(seal_path.read_bytes(), password=None)
        if not isinstance(raw, X25519PrivateKey):
            raise ValueError(f"{seal_path} is not an x25519 private key")
        seal = raw
    else:
        seal = X25519PrivateKey.generate()
        _write_private(
            seal_path,
            seal.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
    return MachineIdentity(key, seal)


def _seal_kdf(shared: bytes, ephemeral_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_SEAL_INFO + ephemeral_pub + recipient_pub,
    ).derive(shared)


def seal_b64(recipient_pub_b64: str, plaintext: bytes) -> str:
    """Seal a payload to a machine's pinned sealing key: ephemeral X25519 +
    ChaCha20-Poly1305. A fresh key per message makes the zero nonce safe."""
    recipient_raw = _unb64(recipient_pub_b64)
    recipient = X25519PublicKey.from_public_bytes(recipient_raw)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_pub = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key = _seal_kdf(ephemeral.exchange(recipient), ephemeral_pub, recipient_raw)
    ciphertext = ChaCha20Poly1305(key).encrypt(b"\x00" * 12, plaintext, None)
    return _b64(ephemeral_pub + ciphertext)


def verify_seal_attestation(
    pubkey_b64: str, seal_pubkey_b64: str, signature_b64: str
) -> bool:
    """True iff the identity key behind pubkey_b64 signed this sealing key."""
    try:
        seal_raw = _unb64(seal_pubkey_b64)
    except (ValueError, TypeError):
        return False
    return verify_b64(pubkey_b64, _SEAL_ATTEST_PREFIX + seal_raw, signature_b64)


def verify_b64(pubkey_b64: str, data: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(pubkey_b64)).verify(
            _unb64(signature_b64), data
        )
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False
