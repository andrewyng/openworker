---
id: metered-web-broker
title: metered-web-broker
state: active
updated: '2026-08-28'
tags: []
---
**Now:** metered-web-broker P1+P2 wired and verified: full suite 58 passed, all 8 packages typecheck clean.

## History
- 2026-08-28 — Wire contract fix (2026-08-28): `SelfHosted402Rail.authorize()` was encoding the `x-payment` header as a bare mandate JSON (`b64url(JSON.stringify(mandate))`), but `decodeXPayment`/`encodeXPayment` require the envelope `{ type: 'urn:mwb:self402:payment-mandate:v1', mandate }`. Changed authorize() to call `encodeXPayment(mandate)`. This was the real bug blocking the metered-origin round-trip test (decode threw 'unknown x-payment shape'). The `payload` field of RailAuthorization is still the bare mandate; only the settlement header must be the envelope form. (source: packages/rails/src/selfsettle.ts)
