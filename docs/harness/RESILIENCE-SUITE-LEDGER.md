# Repeatable resilience qualification

October 30, 2026. Base `c3222d27`; target `openworker-harness-sdk`.

## Plan

1. Reuse and extend deterministic runtime/telemetry/UI tests. Add explicit assertions
   for interrupted streams, unknown effects, budgets and process ownership/lifetime.
2. Run real PostgreSQL worker-replacement/control/recording tests, including production
   worker processes on Linux. No Kubernetes required for this level.
3. Add a deterministic Kubernetes/OpenShell qualification: disposable local fixture,
   worker faults during model streaming and tool execution, two sessions sharing an
   environment, process stop/expiry/cancellation and environment cleanup, correlated
   recording/API/export evidence plus management UI smoke checks.
4. Provide one entry command for each level, private raw results and a sanitized report.
   Add a reviewer test catalog grouping cases, rationale, prerequisites and limits.
5. Run all levels locally, retain failures, fix defects uncovered, merge verified code
   into `openworker-harness-sdk`, then push. Do not provision cloud resources.

## Test contract

- Deterministic model fixture controls fault timing. No paid model key required.
- Never replay an uncertain tool action. Recovery yields an unknown-outcome tool
  response; the next model decision can inspect before deciding whether to act.
- Persist budgets and event cursors across ownership changes.
- Turn cancellation does not imply killing an environment-owned server. Explicit
  process stop, deadline, and environment deletion are tested separately.
- Shared workspaces are cooperative, not a tenant isolation boundary. Separate
  directories/managers reject cross-workspace handles; same-workspace agents may
  intentionally share handles. Separate environments remain isolated.
- Destructive cluster tests require an owned fixture, explicit local context and
  namespace binding. Cleanup targets original owned identities only.
- Missing prerequisites are reported as skipped/blocked, never as passes.

## Progress

- Plan recorded before implementation. Existing coverage found in runtime budget and
  telemetry tests, PostgreSQL production-worker recovery tests, process continuity
  Docker qualification, and management observability fixtures. Extend and compose
  these rather than duplicate the production recovery code.

### Implementation checkpoint

- Added three-level entry point and destructive-fixture safety tests; cluster
  faults require the exact service identity and a fresh local resilience namespace.
- Added opt-in, bounded scripted stream barrier (`test-fixtures` Cargo feature).
  Standard runtime builds do not contain this new fault-injection branch.
- Added real Linux process-group tests for expiry without status polling, descendant
  cleanup, retained logs, and one workspace shutting down without stopping another.
- Added SQLite restart-twice unknown-outcome/budget/cursor regression and real
  PostgreSQL worker-death-while-streaming test. Existing uncertain-write recovery
  now explicitly asserts reservation preservation and removal of the replay queue.
- New test found SQLite recovery lacked PostgreSQL's model-visible unknown response.
  Fixed it to retire only started/no-result calls, keep unstarted calls distinct,
  retain budgets and append the recovery response once. Regression now passes.
- Native fast level initially hit sandbox-denied loopback fixture sockets; rerun
  with local socket permission passed (72 runtime unit, 22 contracts/model/telemetry,
  23 recording/export and five JavaScript checks). Export-feature checks follow.
- Linux archive initially included AppleDouble metadata in skill fixtures. Excluded
  macOS extended attributes; corrected Linux runtime run passes 76 tests, including
  all process-group tests. This was fixture transfer, not a product skill failure.
- PostgreSQL startup check initially observed initdb's temporary Unix-socket server.
  Changed readiness to require its final TCP listener; retained the failed receipt
  and started a fresh isolated test attempt. No uncertain database creation replay.

### Repeatability fixes during qualification

- Qualification image now includes `procps`; existing Linux worker tests invoke the
  external `kill` utility. Its absence caused teardown failures, not lost effects.
- Interaction fixture retries only the explicit pre-admission "checkpointing" busy
  rejection, with the exact original interaction answer and a bounded deadline.
  Tool calls and ambiguous failures are never retried.
- Test-only database timeout/lease changed together from 500ms/3s to 1s/6s; checks
  that wait over two leases now wait 13s and manual lease-expiry waits are 6.2s.
  Production settings unchanged. This targets deterministic faults rather than
  incidental subsecond database failures under local load.
- First cluster attempt reached a definitive HTTP rejection before session creation
  because its profile had not been activated; preparation now activates it first.
- Next admission timed out in OpenShell while waiting for the gated workload Pod.
  Gateway rolled back the original CR. The original ID was read-only reconciled as
  NotFound; its service had zero sessions. Removed only that empty owned namespace,
  preserving receipts and marking the attempt failed. No CreateSandbox or tool call
  was replayed. A new namespace/identity is a separate qualification attempt.
- The kind preparer checks imported image contents and cluster ownership before
  registering immutable digest aliases, avoiding accidental registry pull attempts.

### Final source checks

- Linux runtime/SQLite suite: 76 unit tests plus 18 core integration cases pass.
- Strict all-target Clippy passes natively and on Linux for runtime/PostgreSQL with
  the opt-in test-support feature. The base compiler image lacked Clippy, so the
  disposable lint container installed its official pinned-toolchain component.
- Reviewer guide now groups the cases into streams/recovery, uncertain effects,
  shared processes, cleanup, observability and UI/safety. Run commands and the
  distinction between unit, database and real cluster evidence are explicit.
- Current PostgreSQL qualification has passed multi-session database loss,
  worker death, scoped approvals and graceful/deadline drain scenarios; the final
  complete report, process-manager checks and cluster run are still pending.

- Final fast entry command passes: **131 Rust tests and five JavaScript tests**,
  with all exporter features enabled. Linux adds the native process-group checks
  (76 runtime unit + 18 core tests passed). TypeScript SDK rebuild passes.
- A durable marker now permits only one destructive qualification per prepared
  service, so rerunning a failed command cannot repeat an uncertain workload.
- Cluster worker faults use zero-grace deletion of the exact owned worker pod;
  qualification does not rely on unprivileged signalling of namespace PID 1.

### Cluster-detected service result mismatch

- Fresh second fixture successfully provisioned OpenShell and ran two independent
  sessions/HTTP servers. Start events were recorded for both. Worker pod was killed
  during the controlled partial stream; canonical turn recovered as interrupted
  with its original budget intact.
- Public run projection mapped submission phase `interrupted` to `failed`, although
  both SDKs support the interrupted status. Added explicit projection of interrupted
  plus its failure reason, and a real PostgreSQL regression for current and older
  run receipts. Retain this failed assertion as evidence; do not relabel it passed.

- Cleanup after the injected crash exposed another missing path: shutdown skipped
  environment-bound sessions when a recovered runtime had no in-memory client or
  local sandbox. Added environment ownership to that cleanup guard. A real
  PostgreSQL test now verifies old-epoch attachments are released with unknown
  outcome while the shared environment remains ready and its session stays open.
- Failed-attempt cleanup uses an explicit process_status read of the saved original
  handle to retire the old bookkeeping, then normal session-close/environment-delete
  APIs. No server launch or uncertain action is repeated. The failed test is retained.

- First complete PostgreSQL level passed: 26 Rust tests (including all 15 worker
  faults) and the network-disabled process-manager qualification. Owned test
  containers and generated credentials were removed. After the cluster-discovered
  fixes, rerun state/environment and worker checks against the new image.
- Fast and database checks are complete before actual cluster provisioning/faults
  to reduce incidental load on the local Kubernetes/OpenShell fixture.
