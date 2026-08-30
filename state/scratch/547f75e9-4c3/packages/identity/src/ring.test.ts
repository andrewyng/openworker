import { describe, expect, it } from 'vitest';
import { frozenClock } from '@mwb/core';
import type { Clock } from '@mwb/core';
import { KeyRing, PACT, issuePact } from '@mwb/identity';

const clock: Clock = frozenClock('2026-08-18T12:00:00.000Z');

function ringClock(ms: number): Clock {
  return { now: () => new Date(ms).toISOString() };
}

const T0 = Date.parse('2026-08-18T12:00:00.000Z');

beforeEachReset();

function beforeEachReset() {
  // Each test gets its own KeyRing; nothing global to reset. Keep for symmetry.
}

describe('KeyRing', () => {
  it('issues, signs, and verifies with real Ed25519', () => {
    const ring = new KeyRing(clock);
    const kp = ring.issue({ keyId: 'key_a' });
    expect(kp.algorithm).toBe('ed25519');
    expect(kp.publicRaw.length).toBeGreaterThan(0);

    const headers = { host: 'news.example.com', date: 'Wed, 18 Aug 2026 12:00:00 GMT' };
    const { signature, message } = ring.sign('key_a', headers);
    const sig = signature.match(/signature="([^"]+)"/)?.[1]!;
    expect(ring.verify('key_a', message, sig)).toBe(true);
    expect(signature).toContain('keyId="key_a"');
    expect(signature).toContain('algorithm="ed25519"');
    expect(signature).toContain('fields="date host"');
  });

  it('caps the TTL at 24h', () => {
    const ring = new KeyRing(clock);
    const kp = ring.issue({ keyId: 'key_cap', ttlMs: 25 * 60 * 60 * 1000 });
    const ttl = Date.parse(kp.expiresAt) - T0;
    expect(ttl).toBeLessThanOrEqual(24 * 60 * 60 * 1000);
  });

  it('rotate marks the old key as revoked but it still verifies', () => {
    const ring = new KeyRing(clock);
    ring.issue({ keyId: 'key_1' });
    const headers = { host: 'x.example.com', date: 'd' };
    const { signature, message } = ring.sign('key_1', headers);
    const sig = signature.match(/signature="([^"]+)"/)![1];

    const { previous, next } = ring.rotate({ keyId: 'key_2' });
    expect(previous?.keyId).toBe('key_1');
    expect(previous?.revokedAt).toBeDefined();
    expect(next.keyId).toBe('key_2');

    // In-flight settlement: old key still verifies.
    expect(ring.verify('key_1', message, sig)).toBe(true);
    // But signing with the old key is refused.
    expect(() => ring.sign('key_1', headers)).toThrow(/revoked/i);
    expect(ring.active().map((k) => k.keyId)).toEqual(['key_2']);
  });

  it('directory contains only active keys in stable order', () => {
    const ring = new KeyRing(clock);
    ring.issue({ keyId: 'key_b' });
    ring.issue({ keyId: 'key_a' });
    ring.issue({ keyId: 'key_revoked' });
    ring.get('key_revoked').revokedAt = clock.now();
    const dir = ring.directory();
    expect(dir.format).toBe('agent-identity-directory');
    expect(dir.agentIdentity.map((k) => k.keyId)).toEqual(['key_a', 'key_b']);
  });
});

describe('PACT', () => {
  it('issues a 3-part token and verifies against the ring', () => {
    const ring = new KeyRing(clock);
    ring.issue({ keyId: 'key_pact' });
    const body = {
      subject: 'agent:acme-bot',
      grants: [{ domain: 'news.example.com', spendCeilingMicros: 100_000 }],
      iat: new Date(T0).toISOString(),
      exp: new Date(T0 + 15 * 60_000).toISOString(),
      iss: 'metered-web-broker',
    };
    const { token, message } = PACT.issue(ring, 'key_pact', body);
    const [b64, keyId, sig] = token.split('.');
    expect(b64).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(keyId).toBe('key_pact');
    expect(sig).toMatch(/^[A-Za-z0-9_-]+$/);

    const parsed = PACT.parse(token);
    expect(parsed.claims).toEqual(body);
    expect(parsed.keyId).toBe('key_pact');
    expect(parsed.body).toBe(message);

    const vp = PACT.verify(token, ring);
    expect(vp.valid).toBe(true);
    expect(vp.claims).toEqual(body);
  });

  it('rejects a PACT after its expiry, even if the signature is valid', () => {
    // Ring at a later clock that re-issues under a fresh key (so the
    // signature is valid under the later clock's ring) but the claims
    // are expired against the later clock.
    const later = new KeyRing(ringClock(T0 + 60 * 60_000));
    later.issue({ keyId: 'key_late' });
    const body = {
      subject: 'agent:x',
      iat: new Date(T0).toISOString(),
      exp: new Date(T0 + 15 * 60_000).toISOString(),
      iss: 'mwb',
    };
    const { token } = PACT.issue(later, 'key_late', body);
    const res = PACT.verify(token, later);
    expect(res.valid).toBe(false);
    expect(res.reason).toBe('pact-expired');
  });

  it('rejects a PACT signed by a different keyId', () => {
    const ring1 = new KeyRing(clock);
    const ring2 = new KeyRing(clock);
    ring1.issue({ keyId: 'k1' });
    ring2.issue({ keyId: 'k1' }); // different seed under the same keyId
    const body = {
      subject: 'agent:z',
      iat: new Date(T0).toISOString(),
      exp: new Date(T0 + 60 * 60_000).toISOString(),
      iss: 'mwb',
    };
    const { token } = PACT.issue(ring1, 'k1', body);
    const res = PACT.verify(token, ring2);
    expect(res.valid).toBe(false);
    expect(res.reason).toBe('signature-mismatch');
  });
});

describe('issuePact', () => {
  it('fills subject / grants / iss', () => {
    const ring = new KeyRing(clock);
    ring.issue({ keyId: 'subject-key' });
    const { token } = issuePact(ring, 'subject-key', 'agent:x', [
      { domain: 'news.example.com', scope: 'fetch', spendCeilingMicros: 100_000 },
    ]);
    const parsed = PACT.parse(token);
    expect(parsed.claims.subject).toBe('agent:x');
    expect(parsed.claims.grants).toHaveLength(1);
    expect(parsed.claims.iss).toBe('metered-web-broker');
  });
});
