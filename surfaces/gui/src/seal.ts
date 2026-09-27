// Browser-side sealing (OPE-149) — byte-for-byte the same format as
// coworker/remote/identity.py::seal_b64, so the box's existing unseal opens
// it unchanged:
//
//   ephemeral X25519 keypair → shared = X25519(eph_priv, machine_seal_pub)
//   key   = HKDF-SHA256(shared, salt=∅, info="openworker-seal-v1"‖eph_pub‖recipient_pub)
//   blob  = eph_pub(32) ‖ ChaCha20-Poly1305(key, nonce=12×0).encrypt(plaintext)
//
// A fresh ephemeral key per message is what makes the zero nonce safe.
// Plaintext exists only in this tab and on the box — the cloud relays the
// base64 blob and stores names/hashes, never values.

import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { x25519 } from "@noble/curves/ed25519.js";
import { hkdf } from "@noble/hashes/hkdf.js";
import { sha256 } from "@noble/hashes/sha2.js";

const SEAL_INFO = new TextEncoder().encode("openworker-seal-v1");

const b64decode = (s: string): Uint8Array =>
  Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

const b64encode = (bytes: Uint8Array): string => {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
};

const concat = (...parts: Uint8Array[]): Uint8Array => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
};

/** Seal `plaintext` to a machine's pinned sealing key (its base64 public
 * half, from the machines list). Returns the base64 blob the acceptor
 * relays as a `secret_deploy` frame. */
export function sealTo(recipientPubB64: string, plaintext: Uint8Array): string {
  const recipientPub = b64decode(recipientPubB64);
  if (recipientPub.length !== 32) throw new Error("bad sealing key");
  const ephPriv = x25519.utils.randomSecretKey();
  const ephPub = x25519.getPublicKey(ephPriv);
  const shared = x25519.getSharedSecret(ephPriv, recipientPub);
  const key = hkdf(sha256, shared, undefined, concat(SEAL_INFO, ephPub, recipientPub), 32);
  const ciphertext = chacha20poly1305(key, new Uint8Array(12)).encrypt(plaintext);
  return b64encode(concat(ephPub, ciphertext));
}

/** Seal a profiles payload for deploy: {profiles: {name: data}} — the exact
 * JSON the box-side `handle_secret_deploy` expects to unseal. */
export function sealProfiles(
  recipientPubB64: string,
  profiles: Record<string, Record<string, unknown>>,
): string {
  return sealTo(recipientPubB64, new TextEncoder().encode(JSON.stringify({ profiles })));
}

/** sha256 hex of a value — the deploy ledger's staleness reference. Not a
 * canonical-JSON match for the wallet's hash (different serializers); on a
 * wallet-less deployment nothing ever compares them across sources. */
export async function profileHash(data: Record<string, unknown>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  return Array.from(sha256(bytes))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
