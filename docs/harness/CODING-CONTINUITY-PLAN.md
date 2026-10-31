# Coding turns: budgets, correctable errors and environment-owned processes

October 30, 2026. From `019e6b68`; integration target `openworker-harness-sdk`.
User authorized all three fixes, telemetry, local OpenShell acceptance and merging
verified changes. No paid cloud provisioning; cumulative GCP allowance stays US$100.

## Contract

- Agent defaults and per-turn overrides configure LLM-call/step and total output
  token budgets. Resolve/persist policy and consumption with the turn. Stop further
  tool effects at exhaustion, give the model a content-free limit diagnostic and
  allow one bounded tool-free summary using an explicitly reserved allowance.
  Exhaustion is visible to the user and telemetry, not a silent generic failure.
  A budget never authorizes tool replay, bypasses approval or relaxes isolation.
- Invalid model tool arguments that have not executed return a durable structured
  tool response so the model can correct them. Unbound tools, hard policy denials,
  transport/storage failures and uncertain outcomes remain distinct. Repeated
  invalid calls are bounded. Record safe telemetry; do not export raw arguments.
- Keep `run_shell`, `process_start`, `process_status`, `process_logs`, `process_stop`.
  No separate server tool. A sandbox-local manager owns processes across turn/MCP
  attachments. Stable handles are scoped to that manager/workspace generation;
  never accept raw PIDs, replay a launch, silently restart or grant more access.
  Bound lifetimes/logs/process counts; stop groups on explicit stop/expiry/shutdown.
  A sandbox/manager crash is not process recovery. Surface stale/unknown outcomes.

## Order and acceptance

1. Map protocol, Rust/TypeScript agent/turn APIs, storage, model usage and telemetry.
   Implement persisted budgets and safe argument diagnostics with deterministic
   tests (limits/overrides/recovery, summary reserve, no effects on invalid calls).
2. Implement environment-owned coding process service and protocol/client. Test
   reconnect across independent MCP clients, logs, timeout/stop, simultaneous
   starts, generation/ownership, unknown launch reply and no automatic replay.
3. Build matching Linux runtime/service/developer images locally, import by digest
   into the existing kind cluster, use an isolated service namespace/DB/workspace.
   Run a real web-app build exceeding 20 steps, follow-up edit and private preview.
   Exercise a low budget and invalid arguments; check events and persisted metrics.
   Check that model keys remain worker-only. Preserve NVIDIA demo and old receipts.
4. Update API/tool docs and the prior acceptance ledger with actual results. Run
   appropriate Rust/TypeScript/Helm regressions, review changes, merge/push only the
   verified milestone. Retain failed evidence; never reset it to fabricate a pass.

## Ledger

- Plan recorded before code changes. Previous acceptance found a 20-step failure,
  a schema failure and a stale handle/stalled endpoint on the following turn.
- Next: inspect API/storage/telemetry boundaries and implement deterministic fixes.

## Implementation checkpoint

- Agent defaults and per-turn limits are wired through Rust, TypeScript, the local
  host protocol and remote service. Limits/usage are durable in SQLite/PostgreSQL;
  queued PostgreSQL requests retain overrides and bind them to idempotency keys.
  Main retries, compaction and auto-review count against the turn allowance. One
  extra tool-free wrap-up call uses a reserve inside the output-token total.
- Invalid schema/relative-path requests now return an unexecuted structured tool
  error; five consecutive invalid calls stop with a summary. Hard denials/uncertain effects
  retain their distinct handling. Budget/validation events use content-free typed
  telemetry operations; process start/stop/stale events use hashed identities.
- Environment-backed, unbounded-grant service profiles can use the sandbox-local
  process manager. Local attachment cleanup and temporary credential/resource
  profiles retain their original bounded lifetime. No temporary grant is prolonged
  merely to keep a development server alive.
- Native runtime unit tests: **69 passed**. Includes 26 model requests in one turn,
  step/token stops, one-summary reserve, argument correction, bounded invalid-call
  recovery, idempotency binding, and content-free exported stop/error events.
- Real isolated PostgreSQL regressions: **28 passed**, including queued-limit
  persistence, idempotency conflicts and admission/storage tests. Initial fixture
  port collision was corrected using a Docker-assigned loopback port; another
  project's database/container was not changed.
- TypeScript: **18 config/transport and 8 service HTTP tests passed**. Native SDK/observability/telemetry regression passed
  with explicit loopback permission; Linux SDK execution checks are still pending.
- First Linux runtime regression build passed its 62 then-current unit tests and
  coding/core/permissions/telemetry suites. A final source build will include the
  subsequently added budget tests and SDK coverage.
- Network-disabled coding-runner acceptance passed: two independent MCP clients,
  healthy HTTP/log output after disconnect, lost launch reply checked by known
  handle without replay, timeout/stop and cross-workspace handle rejection.
- Next: final Linux source/images, real OpenShell build → follow-up → preview,
  budget/error probes and recorded/exported telemetry verification. No paid GCP/AWS deployment. Live-model qualification uses the previously approved provider credential.

### Final verification checkpoint

- Conservative settlement is tested: partial usage from an incomplete provider
  stream cannot refund the reserved output allowance. A successful final usage
  record is required for a refund. All 69 native runtime unit tests pass.
- The 28 disposable PostgreSQL tests pass again, with explicit checks that an
  initial-turn override cannot become a subsequent turn's default. Empty-session
  creation rejects a turn limit; agent releases supply reusable defaults.
- Native strict Clippy passes for runtime, SDK, store, server, telemetry and
  observability (all targets and SDK test support). The final Linux checks run
  sequentially after building matching service/runner release binaries.
- The first standalone OpenShell probe was delayed by local VM contention. Its
  create RPC timed out; the same identity was reconciled, not created again. The
  gateway later rolled back that stale fail-closed bootstrap. Deletion was
  confirmed read-only; this failed receipt remains retained. A second probe lost its connection before obtaining a binding when the gateway
  port-forward ended; that was treated as an unknown creation outcome.
  Neither attempt is counted as a process-continuity pass.
- Final images are imported locally by digest. A fresh isolated service namespace
  and a bounded OpenShell probe use the final runner; real-model acceptance follows.

- Real OpenShell process probe passed with the final runner: the same opaque
  handle and file-reading HTTP endpoint survived attachment closure/reconnection;
  explicit stop and original environment deletion completed. Private receipts
  preserve the earlier failed/unknown probes too.
- The first service admission stopped before model/tool execution. Its original
  remote-name lookup returned NotFound. Only that isolated fixture admission was
  ended as failed with an explanatory record; it was not reset or recreated.
  A distinct second qualification has separate write-once receipts and is running.
- Broader desktop tests found an omitted host send-options allowlist entry, then
  exposed a debug-build stack overflow in nested async frames. The allowlist and
  boundary heap allocation were corrected. The native SDK suite now passes on
  normal Tokio stacks, without increasing the application thread stack size.
- The real model has already returned and corrected invalid file-tool arguments.
  Validation-failure/recovery events are retained in the same running turn.

## Live acceptance results

- Initial build completed after **24 main model requests**, 20 executed tools and
  two corrected validation failures; 28 generated tests passed independently.
- Enabled the separate recorder connection/schema after explicitly migrating it.
  The first build has a durable journal, but was not retroactively recorded.
  Local initializer now preserves explicit optional `observability` config; its
  new regression prevents silently dropping that configuration again.
- Replaced this fixture's API/controller/worker pods with the same image/profile.
  The original sandbox server retained both its opaque handle and OS PID, with
  increasing uptime. Independent file-reading HTTP checks and generated tests pass.
- One follow-up stopped after a connection failure while a tool was in flight.
  That failed run remains failed. A distinct recovery message first inspected
  files/status before finishing remaining work. No tool action was transparently
  replayed. Recovery completed with 55 main requests plus two compactions, well
  beyond the old 20-step limit; **45 generated tests passed independently**.
- A one-call turn refused the queued write with `executed:false`, produced a
  bounded meaningful model summary, and did not create `budget-proof.txt`.
  The following turn made six model requests with the normal agent defaults.
- Recording database contains `turn.budget_exceeded`, `tool.validation_failed`,
  `process.started` and `process.stopped`. The schema-validating provider did not
  make the deliberately malformed probe call; natural correction in the earlier
  recorded recovery supplies that live evidence. Deterministic runtime tests
  separately exercise malformed requests and their content-free export path.
- Live browser checks through verified OpenShell ForwardTcp passed: task creation,
  persistence across reload and agent update, table view, due dates, overdue state,
  inline status changes and summary counts. Browser feedback exposed generated
  CSS overriding `[hidden]`; a bounded new agent turn fixed it and reran all 45
  tests, without restarting the server or resetting browser data.
- Independent kernel-floor check: a public world-readable sentinel is readable by
  ordinary Docker uid 1000 but denied with EACCES inside OpenShell. Tool processes
  contain no model key. The managed server remains the original process.
- Network-disabled container checks also pass simultaneous manager startup,
  independent MCP attachments, HTTP/log snapshots after disconnect, lost launch
  reply checked by known handle without replay, timeout/stop and cross-workspace
  handle rejection.

### Verification and practical limits

- Native runtime: **70 passed**; Linux runtime: **71 passed**. Includes compaction
  exhaustion finishing instead of parking another question, and partial/failed
  stream usage retaining its reservation.
- Linux SDK: **13 passed** across agents, embedded/managed regressions and desktop
  API suites, including agent defaults and per-turn override execution. Native SDK
  suite passes with ordinary Tokio thread stacks. PostgreSQL-specific SDK cases
  remain separate; actual service SDK tests and 28 disposable PostgreSQL tests pass.
- Linux coding/core/permissions: **32 passed**, one opt-in HTTP fixture ignored.
  TypeScript: **18 config/transport + 8 service tests passed**. Helm/local setup:
  **11 passed**, including worker-only model-key mounting and optional recording.
- Linux functional checks run in the previously authorized dedicated test VM,
  with one compiler job and debug symbols disabled for test artifacts. Compiling
  large binaries alongside the 8 GiB Docker Kubernetes VM caused local connection
  failures; do not co-schedule those builds in this small fixture. Production
  release settings are unchanged. The optional Docker qualification stage uses
  the same bounded build settings and installs its required pinned Clippy tool.
- Recording is best effort and content-free by default. This exercise verifies
  persistence and the existing export tests, not a newly configured Langfuse run.
  Same-image worker replacement is not physical-node or runner-profile upgrade
  qualification. Earlier failed admissions/runs and private raw receipts remain.
- No paid GCP/AWS infrastructure or IAM changes. Cumulative GCP allowance remains
  US$100. Live model requests use the previously approved provider credential.

Sanitized aggregate evidence: [coding-continuity-20261030.json](evidence/coding-continuity-20261030.json).

- Final checks: Linux strict Clippy (all targets, six affected crates) passes;
  the Linux approval/resume telemetry integration also passes. All 140 executed
  Linux functional tests pass; ignored PostgreSQL/HTTP fixtures are explicit.
  Native strict Clippy and the complete native SDK suite pass as well.
- Final cleanup reconciled an originally unbound standalone probe by its exact
  saved intent/profile. It had appeared after the connection failure. Its original
  environment was deleted with a saved delete intent and no repeated creation or
  tool launch. Unknown delivery was never treated as proof nothing happened.

## Integration result

Verified implementation `4dc2a97c` merged into `openworker-harness-sdk` as
`ff874e3d`. The merge tree matches the tested feature tree; no additional runtime
changes were introduced during integration. Local qualification and retained
limitations above apply to this merge.
