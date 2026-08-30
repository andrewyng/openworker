---
id: metered-web-broker-broker-identity-contract
title: metered-web-broker broker/identity contract
state: active
updated: '2026-08-27'
tags: []
---
**Now:** All 3 test failures fixed (2026-08-27): ledger.test.ts spend line (1000→10000 micros), broker.test.ts identity test (now passes opts + sets identity.subject so PACT issues), license engine slugFromUrl (href→pathname so id=/r/73). 38/38 tests pass. scripts/demo.ts added: runs `npm run demo`, walks all 4 terminal outcomes against in-memory rail with frozenClock + injected origin client, renders dashboard after each.

## History
- 2026-08-27 — In metered-web-broker, the Broker only attaches an `identity` PACT token to a fulfilled outcome when BOTH a KeyRing is present AND `BrokerOptions.identity.subject` is set (broker.ts:381-394). A ring alone produces `keyId` in the signature headers but NO `pactToken`. So a test that only passes a ring expects `o.identity?.keyId` truthy but `pactToken` undefined. The terminal-class mapping also depends on this: a 403 with identity present → token-rejected; a 403 with no ring/identity → blocked. (source: packages/gateway/src/broker.ts)
