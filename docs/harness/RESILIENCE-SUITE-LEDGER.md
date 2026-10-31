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

- Static review of the run-scoped telemetry assertion found PostgreSQL recovery
  events relied on a Tokio task-local turn context while running on the separate
  database actor thread. Explicitly bind their turn IDs; a real database test checks
  exactly one recovery/unknown event, correct turn hash and no prompt/argument leak.
- The local teardown helper now observes a bounded read-only pod list, accepting
  an already-empty API selector and failing on API errors. Three unit tests cover
  that race. Normal cleanup of the previous failed namespace now completes.

- A repeated worker-death case caught a fixture race: it killed the process when
  the marker file existed, before the first write necessarily completed. The
  barrier now waits for the complete expected marker contents before injecting
  the fault. The product correctly reported unknown; this corrects the test's
  claim that a known side effect had already happened. Full worker qualification
  has a bounded 15-minute allowance on constrained local hosts.

### Final qualification candidate

- Final fast run passes **131 Rust and eight JavaScript tests**. The first attempt
  was sandbox-denied on five loopback HTTP fixtures; the permitted rerun passes.
  Strict native runtime/PostgreSQL all-target Clippy also passes after the recovery
  correlation change. No dependencies or cloud resources were added.
- Fresh cluster fixture remains empty while the complete PostgreSQL level runs.
  Its image update required replacing only its already-completed bootstrap Job
  because Kubernetes Job templates are immutable. Ownership, completed status and
  zero sessions/environments were checked before replacement. The suite does not
  claim this manual fixture update as Helm upgrade qualification.

- Final complete PostgreSQL level passes: **34 Rust tests** (17 state/environment,
  one recorder outage, one authorized query and all 15 production-worker faults),
  plus the network-disabled process-manager checks. This includes the exact-effect
  barrier fix and the database-actor recovery correlation regression. Owned
  PostgreSQL/worker containers and generated credentials were removed. Failed
  earlier attempts remain distinct private receipts.

### OpenShell signal-denial defect

- Third cluster fixture passes shared servers, both real worker crashes, one-time
  unknown-effect recovery with explicit read-back, correct recovery telemetry and
  cross-API cancellation. It then fails the actual descendant-expiry assertion:
  a delayed child writes its marker despite a `timed_out` result.
- New explicit diagnostics in the original environment show group signals return
  EPERM; individual-child signals work. Stable pidfd acquisition is unavailable.
  The previous Group drop silently ignored signal failure. Retain the failure;
  do not rerun the uncertain workload or weaken OpenShell's policy.
- Dedicated sandbox process managers now establish Linux subreaper ownership.
  On denied group signalling, the root is killed by its still-reserved child PID,
  then only kernel-adopted direct children in that original group are killed and
  reaped. Unreaped children and root reserve the IDs during cleanup. SDK embedding
  does not change the application-wide adoption setting. Unconfirmed cleanup is
  returned as `process_outcome_unknown`, not a false stop/expiry success.
- Added a disposable Linux test process with a real seccomp filter reproducing
  negative-PID EPERM. It checks expiry and normal-exit descendants, sibling
  survival, explicit stop, no zombies and honest unknown cleanup for an embedded
  caller. The first focused regression passes; full rebuild/reruns follow.

- Full Linux rebuild passes **77 runtime unit tests and 18 core cases**, including
  real seccomp denial, plus strict runtime/PostgreSQL Clippy. The failed cluster's
  original sessions and environment were closed/deleted normally. Ingress reached
  its configured 90-second termination grace just after the local helper's equal
  deadline; read-only reconciliation found it gone. Allow a bounded 30-second
  margin (120 seconds total), then finish normal owned-namespace cleanup.

### Database fault-fixture isolation

- A second full database run passes state/recording and the first eleven worker
  cases, then hits a graceful-stop deadline and later migration/RPC timeouts. It
  remains a failed attempt, despite the preceding complete pass. Product process
  cleanup is independently qualified in the fresh OpenShell fixture.
- Docker worker qualification now runs each exact Rust case in a fresh PostgreSQL
  container and Linux process tree, with bounded memory/temp storage. A case cannot
  leave signal/runtime state, connection actors or schema/catalog/WAL accumulation
  for the next case. Per-case PostgreSQL logs/state are captured before cleanup.
  This also makes subsequent timing failures diagnosable. All 15 expected cases
  must run, including the new streaming death case; no failure is retried.
- Cleanup verifies exact ownership labels and confirmed absence. A Docker daemon
  error is not treated as deletion. Cancellation stops admission and cleans the
  current original fixture. Final full isolated run is still pending.

### Collector observation bound

- Fresh OpenShell run passes all six action/fault groups, including the corrected
  real descendant expiry, sibling survival, stop, cancellation and budget refusal.
  Export comparison then times out. Read-only inspection of the original run finds
  all twelve recorded spans present in the actual collector with matching IDs.
- The test had reused an installer command helper that caps stdout at 2 MB; the
  collector file grew beyond that and its JSON was truncated during observation.
  Read the bounded 32 MiB collector volume through an explicit 40 MiB capture, and
  save the recorded timeline before comparison. This changes the test observer,
  not the exporter. Preserve the failed attempt and clean only its original
  resources; prepare a new fixture rather than replay its workload.

- First isolated case passes, but cleanup rejects Docker's lowercase `no such
  object` response for its already-auto-removed worker. Added a shared,
  case-insensitive absence classifier with a regression proving daemon/permission
  errors still fail closed. Reconciled only the saved original container IDs and
  credentials; the failed report remains unchanged. All nine JavaScript checks
  pass before the next full attempt.
