# Guardrails and compliance: test catalog

This catalog explains what the regression suite proves. The
[ledger](GUARDRAILS-AND-HOOKS-PLAN.md) records exact qualification results; the
[QuickStart](GUARDRAILS-QUICKSTART.md) explains the API.

## 1. Detector and transformation rules

**What:** card-length/Luhn validation; common/custom secret patterns; bounded
regex compilation; Unicode byte offsets; exact model-returned fragments;
overlapping matches; masking; unchanged/incomplete callback replacements;
rechecking transformed content; sanitizing callback block feedback; inheritance
and retry caps. Missing handlers, panic, cancellation, timeout and malformed
results all fail closed.

**Why:** a declared rule must actually cover the final candidate. A transform or
callback must not become a way around another installed rule. These deterministic
tests qualify the contract, not a model's ability to recognize every form of PII.

**Source:** `harness/guardrails/src/tests.rs`.

## 2. Runtime boundaries and safe history

**What:** input rejection before admission/provider calls; output redaction before
public deltas/history; bounded regeneration from accepted history; persistent
block termination; before-model denial without a phantom attempt or working-model
charge; before-tool denial in bypass; completed-action receipts when output is
blocked; staged internal memory results and restart without replay; turn-scoped
rules and idempotency; metadata-only observer delivery.

**Why:** a check that runs after content has escaped, or reruns an already executed
action, is not an effective enforcement boundary. Tests inspect snapshots, events,
history, real dispatch counts and working-model request counts using sentinel text.

**Source:** `harness/runtime/src/runtime_safety_tests.rs`. The full runtime unit
suite also covers existing budgets, retries, approval grants, cancellation,
compaction, planning and resource-policy precedence.

## 3. SDK/process/provider parity

**What:** Rust embedded and actual managed-child execution with synthetic Responses,
Chat Completions and Anthropic HTTP/SSE servers. TypeScript uses an actual Rust
child over private stdio. Tests cover input/output callbacks, guarded streaming,
per-turn scope, unavailable-check interruption/resume, cancellation, killed-child
restart and handler re-registration. Bridge tests reject wrong owners and late
responses and enforce bounded callback capacity.

**Why:** the public SDK must enforce the same policy across process and provider
choices. Testing a mocked JavaScript transport alone would not catch native
serialization, process restart or provider streaming mistakes.

**Source:** `harness/harness-sdk/tests/safety.rs`,
`harness/harness-sdk/src/safety_callbacks.rs`,
`harness/typescript-sdk/test/safety.test.mjs`.

The unchanged DLAI tool-free suite is also run: registration, streamed plain text,
and rejection of implicit/unregistered tools across all three provider protocols.

## 4. Durable evidence and permissions

**What:** SQLite/PostgreSQL metadata projection, pagination, reopen/reconnect,
transactional evidence failure, separate metadata/content roles, session scope,
read auditing and API behavior across two nodes. API tests reject protected input
before writing submissions. SQL queries prove the sentinel did not enter the
outbox or event journal. Rust SDK checks run with both storage adapters.

**Why:** evidence must describe committed execution. It cannot depend on a lossy
observability queue, expose raw rejected content or grant transcript access to a
metadata reader. A different API node must produce the same authorized page.

**Source:** `harness/runtime/src/sqlite_compliance.rs`,
`harness/store-postgres/tests/compliance.rs`,
`harness/server-reference/tests/compliance_api.rs`.

## 5. External scanners — mock Prisma only

**What:** local HTTP receivers check Prisma's selected prompt/response request,
profile, correlation ID and header. Exercise allow, block, unknown/malformed
verdicts, correlation mismatch, 401/429/500, redirects, timeout, cancellation and
oversized replies. End-to-end SDK tests connect the adapter to `beforeInput` and
verify no turn is admitted after a block. No transcript/system/private reasoning
is implicitly attached to an external scan.

**Why:** scanner outage or an invalid verdict must not accidentally permit an
operation. A remote diagnostic must not become an extra channel for leaking
inspected text or credentials.

**Source:** `harness/security-adapters/tests/mock.rs`,
`harness/harness-sdk/tests/security_scanner.rs`.

**Limit:** no live Prisma tenant/profile qualification. Detector semantics and
vendor deployment/authentication must be evaluated separately before production.

## 6. Observer and SIEM delivery resilience

**What:** durable observer receipts with stable IDs; timeout/retry isolation;
SQLite/PostgreSQL export checkpoints; exclusive delivery leases; stale-ack
rejection; receiver outage/reconnect; destination binding; bounded batches;
retention protecting unacknowledged records; backlog admission limits that still
allow completion/cancellation records and export. A local HTTP receiver returns
503 and then succeeds; the same evidence is resent without starting an agent.

**Why:** exporting evidence must never replay a tool or an agent turn. Network
uncertainty can produce duplicate deliveries, so receiver deduplication is part of
the contract. Delivery health must show backlog instead of silently losing records.

**Source:** `harness/compliance/src/delivery.rs`, both storage compliance modules,
`harness/harness-sdk/tests/compliance_export.rs`.

**Limit:** at-least-once delivery, not exactly-once external effects. Metadata
storage conventions are not tamper-proof against database administrators. Global
administrative/pre-session decisions remain available through the security-audit
API; the session exporter exports session activity.

## 7. Real OpenShell execution

**What:** create one fresh shared environment on the explicitly configured local
gateway. A seed session writes a synthetic secret. A separate screened session
reads it through the real remote ToolRunner; its history/events contain the
redaction. A before-tool hook then rejects a write in bypass mode. Check real
`tool_started` records and compliance outcomes; close both sessions and delete the
original environment and disposable schema.

**Why:** local handler tests alone cannot prove that the sandbox transport's
results pass the same trusted-side checks. This verifies the actual boundary
between OpenWorker and OpenShell without deploying a cloud cluster.

**Source:** `harness/store-postgres/tests/safety_openshell.rs`.

The qualified gateway is OpenShell 0.1.3, using the existing isolated local test
installation and a pinned Linux runner image. This is not a qualification of a
new OpenShell release. No GitHub writes or real model/vendor credentials are used.

## 8. UI, packaging and footprint

**What:** administrator Safety tab against real Rust APIs/PostgreSQL; desktop and
mobile layout; expandable metadata; escaping; strict proxy routes; no browser
content endpoint. Build minimal/full configurations, verify explicit capability
rejection, and measure stripped release binaries. Run Rust and TypeScript tests
on macOS and Linux; reuse caches and record disk checks.

**Why:** policy needs an understandable operational view. Optional integrations
must not be silently required by a lightweight build. A JavaScript runtime flag
cannot shrink a native binary already shipped with an app.

**Source:** `harness/typescript-sdk/test/minimal-safety.test.mjs`,
`harness/service-ui/test/compliance.test.mjs`,
`harness/service-ui/test/compliance-live.mjs`, and qualification evidence in the
ledger. This work does not claim new live Windows qualification.

## Run the suite

```sh
harness/tests/safety-qualification.sh fast
OW_PG_TEST_URL=postgres://... harness/tests/safety-qualification.sh storage
harness/tests/safety-qualification.sh cluster
harness/tests/safety-qualification.sh ui
```

`cluster` requires `OW_PROBE_ENDPOINT`, `OW_PROBE_CERTS`, `OW_PROBE_RUNNER`,
`OW_PROBE_IMAGE` and optionally `OW_PROBE_TLS_NAME`. `ui` additionally requires
`OW_PLAYWRIGHT_MODULE`; screenshots go to `OW_COMPLIANCE_UI_OUTPUT` or
`/tmp/ow-safety-qualification`. Use a disposable test database and an acceptance
gateway with capacity. The tests own their fresh identities and do not reset
another installation's receipts, credentials, data or sandboxes.

## 9. Compatibility and control recovery

**What:** preserve SQLite sessions/history through the 7→8 upgrade; preserve
PostgreSQL records through the explicit 5→6 upgrade; reject old-client writes;
retry an unavailable safety check through durable controls; let cancellation win;
recover observer receipts under a new owner and reject stale acknowledgements.

**Why:** a binary that ignores new policy fields must not resume a guarded
session. Restoring a check must not accidentally approve another operation, and
observer retries must survive process ownership changes without losing identity.

**Source:** runtime `tests/core.rs`, PostgreSQL `tests/compliance.rs` and
`tests/controls.rs`. Follow the coordinated-upgrade procedure in the QuickStart.

The SDK also has a separate HTTP/SSE privacy-model fixture that returns an exact
spelled-out identifier span. It verifies the configured detector model is called
without tools, blocks before turn admission and does not store the rejected
identifier. This tests the model integration contract, not real-model accuracy.

To qualify an actual minimal release host (in addition to the crate-level
no-engine test), set `OW_TS_MINIMAL_HOST` to its absolute executable path and run
`node --test test/minimal-safety.test.mjs` from `harness/typescript-sdk`. This test
requires no model key and proves required checks are rejected instead of skipped.
