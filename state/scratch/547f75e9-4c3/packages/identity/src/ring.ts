import { KeyObject, generateKeyPairSync, sign, verify } from 'node:crypto';
import { IdentityError, systemClock } from '@mwb/core';
import type { Clock, HeaderRecord } from '@mwb/core';

/**
 * Identity & Attestation — Ed25519 key lifecycle as a first-class concern.
 *
 *  - issue: mint a keypair with tight expiry (default 1h, hard cap 24h)
 *  - rotate: mark the current key revoked, mint the next — old key stays
 *    verifiable for in-flight settlements
 *  - sign: http-message-signatures style signature over selected headers
 *  - PACT: broker-issued attestation token the origin can verify against
 *    the public directory
 *
 * The directory payload below is what the broker serves at
 * `/.well-known/agent-identity` so origins can look up the public key
 * without a side channel.
 *
 * Draft-caveat discipline: build against the *patterns* of Web Bot Auth
 * and PACT, not their draft field names.
 */

export type B64Url = string;

export function b64url(bytes: Uint8Array): B64Url {
  return Buffer.from(bytes).toString('base64url');
}

export function fromB64url(s: B64Url): Uint8Array {
  return new Uint8Array(Buffer.from(s, 'base64url'));
}

export interface KeyPair {
  /** Broker-chosen stable key id — this is what the audit trail references. */
  keyId: string;
  algorithm: 'ed25519';
  /** 32-byte public key (JWK `x`), base64url. */
  publicRaw: B64Url;
  /** 32-byte seed (JWK `d`), base64url. Never leaves the broker. */
  privateRaw: B64Url;
  createdAt: string;
  expiresAt: string;
  revokedAt?: string;
}

export interface IssueOptions {
  keyId?: string;
  /** Lifetime. Default 1h; hard cap 24h (tight-expiry discipline). */
  ttlMs?: number;
}

const MAX_TTL_MS = 24 * 3_600_000;
const DEFAULT_TTL_MS = 3_600_000;

/**
 * The broker's identity vault. Every key the broker has ever issued, in
 * memory. Keys past `expiresAt` or with `revokedAt` set refuse to sign;
 * the ring keeps them so in-flight signatures still verify.
 */
export class KeyRing {
  readonly clock: Clock;
  private readonly keys = new Map<string, KeyPair>();
  private counter = 0;

  constructor(clock: Clock = systemClock) {
    this.clock = clock;
  }

  /** Mint a fresh Ed25519 keypair with tight expiry. */
  issue(options: IssueOptions = {}): KeyPair {
    const now = this.clock.now();
    const nowMs = Date.parse(now);
    const ttl = Math.min(Math.max(1_000, options.ttlMs ?? DEFAULT_TTL_MS), MAX_TTL_MS);
    const { publicKey, privateKey } = generateKeyPairSync('ed25519');
    const pubJwk = publicKey.export({ format: 'jwk' }) as JsonWebKey;
    const privJwk = privateKey.export({ format: 'jwk' }) as JsonWebKey;
    if (!pubJwk.x || !privJwk.d) {
      throw new IdentityError('failed to export Ed25519 JWK', 'IDENTITY_EXPORT');
    }
    this.counter += 1;
    const keyId = options.keyId ?? `key_${this.counter.toString(36)}_${nowMs.toString(36)}`;
    if (this.keys.has(keyId)) {
      throw new IdentityError(`keyId already in use: ${keyId}`, 'IDENTITY_DUPLICATE');
    }
    const kp: KeyPair = {
      keyId,
      algorithm: 'ed25519',
      publicRaw: b64url(fromB64url(pubJwk.x as string)),
      privateRaw: b64url(fromB64url(privJwk.d as string)),
      createdAt: now,
      expiresAt: new Date(nowMs + ttl).toISOString(),
    };
    this.keys.set(keyId, kp);
    return kp;
  }

  /**
   * Rotate: mark the current active key revoked, mint a fresh one. The old
   * key remains verifiable until the caller drops it — that is what lets
   * in-flight settlements close out cleanly.
   */
  rotate(options: IssueOptions = {}): { previous?: KeyPair; next: KeyPair } {
    const current = this.active().sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
    if (current) current.revokedAt = this.clock.now();
    const next = this.issue(options);
    return { previous: current, next };
  }

  get(keyId: string): KeyPair {
    const kp = this.keys.get(keyId);
    if (!kp) throw new IdentityError(`unknown key: ${keyId}`, 'IDENTITY_UNKNOWN');
    return kp;
  }

  all(): KeyPair[] {
    return [...this.keys.values()];
  }

  active(): KeyPair[] {
    const now = Date.parse(this.clock.now());
    return this.all().filter((k) => !k.revokedAt && Date.parse(k.expiresAt) >= now);
  }

  /**
   * Sign a canonicalized header block.
   *
   * Canonicalization: lowercase header names, lexicographic order, one
   * `name: value` per line, joined with `\n`, trailing `\n`. This is the
   * *pattern* of http-message-signatures; the exact wire form tracks
   * whatever draft the origin validates against.
   *
   * Returns:
   *  - `signature`: the wire header value to attach
   *  - `message`: the canonical signing string (for replay/debug)
   *  - `signedAt`: broker timestamp
   */
  sign(
    keyId: string,
    headers: HeaderRecord,
    signedFields: string[] = ['host', 'date']
  ): { signature: string; message: string; signedAt: string } {
    const kp = this.get(keyId);
    const now = this.clock.now();
    if (kp.revokedAt) throw new IdentityError(`key revoked: ${keyId}`, 'IDENTITY_REVOKED');
    if (Date.parse(kp.expiresAt) < Date.parse(now)) {
      throw new IdentityError(`key expired: ${keyId}`, 'IDENTITY_EXPIRED');
    }

    const fields = signedFields.map((f) => f.toLowerCase()).sort();
    const normed = normHeaders(headers);
    const lines = fields.map((f) => {
      const v = normed.get(f);
      if (v === undefined) throw new IdentityError(`missing header to sign: ${f}`, 'IDENTITY_FIELD');
      return `${f}: ${v}`;
    });
    const message = lines.join('\n') + '\n';

    const seed = fromB64url(kp.privateRaw);
    const keyObject = KeyObject.fromJwk({
      kty: 'OKP',
      crv: 'Ed25519',
      d: kp.privateRaw,
    } as JsonWebKey);
    const sigBytes = Buffer.from(sign(null, Buffer.from(message, 'utf8'), keyObject));
    const sig = b64url(new Uint8Array(sigBytes));
    const signature = `keyId="${keyId}";algorithm="ed25519";fields="${fields.join(' ')}";signature="${sig}"`;
    return { signature, message, signedAt: now };
  }

  /** Verify a signature produced by this ring. */
  verify(keyId: string, message: string, sigB64url: B64Url): boolean {
    try {
      const kp = this.get(keyId);
      const keyObject = KeyObject.fromJwk({
        kty: 'OKP',
        crv: 'Ed25519',
        x: kp.publicRaw,
      } as JsonWebKey);
      return verify(null, Buffer.from(message, 'utf8'), keyObject, Buffer.from(fromB64url(sigB64url)));
    } catch {
      return false;
    }
  }

  /**
   * Directory the broker serves at `/.well-known/agent-identity`. Only
   * active keys appear, sorted by keyId for stable diffs.
   */
  directory(): {
    format: 'agent-identity-directory';
    agentIdentity: Array<{ keyId: string; alg: 'Ed25519'; pubkey: string; expires: string }>;
  } {
    return {
      format: 'agent-identity-directory',
      agentIdentity: this.active()
        .slice()
        .sort((a, b) => a.keyId.localeCompare(b.keyId))
        .map((k) => ({
          keyId: k.keyId,
          alg: 'Ed25519' as const,
          pubkey: k.publicRaw,
          expires: k.expiresAt,
        })),
    };
  }
}

function normHeaders(headers: HeaderRecord): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(headers ?? {})) out.set(k.toLowerCase(), v);
  return out;
}

/**
 * A PACT is a broker-issued, key-signed attestation token. Wire format is
 * intentionally minimal:
 *
 *   <base64url(json of claims)> . <keyId> . <base64url(signature)>
 *
 * where the signature covers `<claimsBytes>` exactly (so origin replays
 * the same bytes it received, and the keyId selects the ring entry). When
 * the PACT spec stabilizes, this token shape stays — only the payload
 * inside changes.
 */
export class PACT {
  static issue(ring: KeyRing, keyId: string, claims: Record<string, unknown>): {
    token: string;
    message: string;
    signedAt: string;
  } {
    const body = JSON.stringify(claims);
    const b64 = Buffer.from(body, 'utf8').toString('base64url');
    const sigB64url = b64url(signPactBody(body, ring, keyId));
    return {
      token: `${b64}.${keyId}.${sigB64url}`,
      message: body,
      signedAt: ring.clock.now(),
    };
  }

  static parse(token: string): { claims: Record<string, unknown>; keyId: string; signature: B64Url; body: string } {
    const parts = token.split('.');
    if (parts.length !== 3) throw new IdentityError('malformed PACT token (expected 3 parts)', 'PACT_MALFORMED');
    const [bodyB64, keyId, signature] = parts as [B64Url, string, B64Url];
    const body = Buffer.from(bodyB64, 'base64url').toString('utf8');
    let claims: Record<string, unknown>;
    try {
      claims = JSON.parse(body) as Record<string, unknown>;
    } catch (cause) {
      throw new IdentityError('PACT claims are not valid JSON', 'PACT_CLAIMS', cause);
    }
    return { claims, keyId, signature, body };
  }

  static verify(token: string, ring: KeyRing): { valid: boolean; reason?: string; claims?: Record<string, unknown> } {
    const { claims, keyId, signature, body } = PACT.parse(token);
    if (new Date(claims.expiresAt as string).getTime() < Date.parse(ring.clock.now())) {
      return { valid: false, reason: 'pact-expired', claims };
    }
    const ok = signPactVerify(body, ring, keyId, signature);
    if (!ok) return { valid: false, reason: 'signature-mismatch', claims };
    return { valid: true, claims };
  }
}

/** Sign the exact bytes of a PACT body (not a header canonicalization). */
function signPactBody(body: string, ring: KeyRing, keyId: string): Uint8Array {
  const kp = ring.get(keyId);
  const keyObject = KeyObject.fromJwk({
    kty: 'OKP',
    crv: 'Ed25519',
    d: kp.privateRaw,
  } as JsonWebKey);
  return new Uint8Array(sign(null, Buffer.from(body, 'utf8'), keyObject));
}

function signPactVerify(body: string, ring: KeyRing, keyId: string, sigB64url: B64Url): boolean {
  try {
    const kp = ring.get(keyId);
    const keyObject = KeyObject.fromJwk({
      kty: 'OKP',
      crv: 'Ed25519',
      x: kp.publicRaw,
    } as JsonWebKey);
    return verify(null, Buffer.from(body, 'utf8'), keyObject, Buffer.from(fromB64url(sigB64url)));
  } catch {
    return false;
  }
}

// PACT helpers — small indirections so KeyRing stays readable. The `keyId`
// is already on the ring, so these resolve via the ring.
function require_ring_sign(_ring: KeyRing): { signature: string } {
  return { signature: '' };
}

function signMessage(keyId: string, message: string, ring: KeyRing): { signature: B64Url } {
  const kp = ring.get(keyId);
  const keyObject = KeyObject.fromJwk({
    kty: 'OKP',
    crv: 'Ed25519',
    d: kp.privateRaw,
  } as JsonWebKey);
  return { signature: b64url(new Uint8Array(sign(null, Buffer.from(message, 'utf8'), keyObject))) };
}

/** Convenience: issue a common-claims PACT with standard expiry. */
export function issuePact(
  ring: KeyRing,
  keyId: string,
  subject: string,
  grants?: Array<{ domain?: string; scope?: string; spendCeilingMicros?: number }>
): { token: string; body: string } {
  const nowMs = Date.parse(ring.clock.now());
  const body = {
    subject,
    grants: grants ?? [],
    iat: new Date(nowMs).toISOString(),
    exp: new Date(Math.min(nowMs + 15 * 60_000, Date.parse(ring.get(keyId).expiresAt))).toISOString(),
    iss: 'metered-web-broker',
  };
  return PACT.issue(ring, keyId, body);
}
