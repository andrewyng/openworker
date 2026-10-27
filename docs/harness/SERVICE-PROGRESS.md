# OpenWorker Service progress ledger

## 2026-10-27 — M1 started

- Created feature branch `feat/openworker-service-m1`; public SDK branch unchanged.
- Read existing REST/SSE reference, PostgreSQL admission/auth, execution provider
  contract and local SDK transports. Reuse these boundaries.
- Created [implementation plan](SERVICE-IMPLEMENTATION-PLAN.md).
- Disk baseline: 38 GiB available; reuse native Cargo target (12 GiB).
- M1 deliberately begins with configured agent/environment bindings. RuntimeProfile
  creation, deployment gates and full management auth/UI remain later milestones,
  advertised as unsupported rather than stubbed success.

Initial state: tests not yet run. See the milestone entry below for results.

## 2026-10-27 — M1 implementation and qualification

Implemented:

- Opt-in `/v1` capabilities, identity, configured-agent lookup, session creation,
  snapshots, idempotent turn admission, run results, controls/receipts and SSE.
- Persistent service/namespace binding; existing PostgreSQL role, ownership and
  environment grants remain authoritative. No public bootstrap route.
- Rust and TypeScript `Harness.connect()` with remote session/run handles,
  credential callbacks, endpoint identity checks, bounded event parsing and cursor
  reconnection. Disconnect closes client connections, not worker execution.
- Mutation transport failures retain their idempotency key and never trigger an
  automatic resubmission. The legacy queue API and local SDK transports remain.
- [M1 developer setup and API examples](SERVICE-M1-QUICKSTART.md).

Issues found during actual qualification:

1. Mac controller validation incorrectly required a Mach-O ToolRunner even when
   OpenShell executes that runner in Linux. Validation now follows execution
   location; unsandboxed runners still require the host executable format.
2. A remote run iterator stopped on `turn_recovered`, although a worker can recover
   a parked turn and continue after applying approval. Both SDKs now stop only on
   terminal recovery statuses. The end-to-end test covers replay through recovery
   to the real tool result.
3. The existing cached Linux qualification image predates the `example_echo`
   example-tool rename. The test builds a temporary bundle with supported file and
   shell tools. It uses the cached pinned runner unchanged; no large image rebuild.

Verification completed so far:

- TypeScript: 17 existing configuration/stdio transport tests, 6 service tests.
- Rust SDK: 16 unit tests; desktop API suite 5 passed (embedded SQLite,
  managed process, ephemeral state, provider/reviewer setup, callback crash).
  The two PostgreSQL desktop variants are separately opt-in and not counted.
- PostgreSQL: 3 ingress, 8 submission, and 1 service-identity/recovery regression
  tests; 5 adapter unit tests. The identity test rejects writes via a database
  trigger and verifies that restore identity checks still succeed.
- Platform: execution-location runner-format test passed.
- Server reference: 4 existing unit tests passed. Recovery-only startup verifies
  the persisted service binding without inserting or updating it.
- Real service qualification passed against disposable Docker PostgreSQL,
  two native API processes, a native worker and a sandbox on the existing local
  Kubernetes/OpenShell gateway. Deterministic model, real file write/read.
  Rust creates/submits/approves, original API exits, TypeScript resumes and reads
  output, replays events, cancels a subsequent pending write, and reads old results.
- Final augmented real-service run passed (19.75 seconds), including observer
  mutation denial, cross-owner access, missing environment grants and result text.

Local resources: reused `postgres:16-alpine` with a 256 MiB tmpfs and existing
OpenShell runner image. Failed-attempt sandboxes were explicitly deleted; successful
fixture cleans its sandbox and schema. No existing DLAI/other workloads changed.
Disk available increased to ~71 GiB during the session; no extra space needed.

Remaining after M1: deployed agent releases, RuntimeProfile API/provisioning,
shared-environment attachment management, `ows`, packaged local Docker stack,
expanded auth/configuration, Kubernetes control plane and UI. Existing OpenShell
integration is reused; this milestone does not claim those management APIs exist.

Cleanup complete: dedicated test sandboxes removed; only the pre-existing suspended
sandbox remains in the gateway namespace. Disposable PostgreSQL container stopped,
gateway port-forward stopped and temporary copied client certificates removed.
Final disk measurement: 68 GiB available. No cloud spending or new container images.

M1 complete. Changes are local on `feat/openworker-service-m1`; no public branch
push or pull request is part of this milestone.
