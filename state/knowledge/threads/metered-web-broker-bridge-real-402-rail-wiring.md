---
id: metered-web-broker-bridge-real-402-rail-wiring
title: 'metered-web-broker bridge: real 402 rail wiring'
state: active
updated: '2026-08-28'
tags: []
---
**Now:**

## History
- 2026-08-28 — Fixed the self-402 settlement rail (the one outstanding "real rail" piece) so it compiles and passes. ROOT CAUSES in packages/rails/src/selfsettle.ts + selfsettle.test.ts: (1) SettlementBackend.settle() was declared sync but all impls were async → made interface async. (2) InProcessSettlementBackend.keyId field-initializer read param property before assignment (TS2729/runtime crash) → moved assignment into constructor body. (3) makeSelf402Rail default options={} violated required priceMicros → default {priceMicros:5000}. (4) decodeXPayment passed spurious 'utf8' arg to Buffer.from(Uint8Array) → removed. (5) res.json() `.reason` on unknown → typed cast. (6) reasonOf m[1] under noUncheckedIndexedAccess → default. (7) fromB64url was lenient so malformed header gave "not JSON" not "malformed" → added base64url round-trip check. (8) PaymentMandate.signature required → optional so delete works; verifyMandate already guards. TESTS: corrected 3 wrong assertions — 88→86 Ed25519 base64url sig length (64 bytes=86 chars unpadded, verified), rec destructuring under noUncheckedIndexedAccess, and the replay test used two DIFFERENT fresh-nonce mandates (fixed to reuse one). Also moved RailError import from @mwb/rails→@mwb/core. (source: packages/rails/src/selfsettle.ts)
