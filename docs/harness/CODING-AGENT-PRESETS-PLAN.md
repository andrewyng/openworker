# Coding agent presets: implementation sequence and progress

Status: C1–C3 verified; continuing the approved C4–C7 sequence. Canonical feature branch: `openworker-harness-sdk`. Use a feature
branch for each slice and merge only verified changes. No public-main merge or
cloud provisioning is part of this plan.

Design: [coding presets, defaults and publication](CODING-AGENT-PRESETS.md).
Underlying primitives: [team design](TEAM-TYPES-AND-SANDBOXES.md) and
[team implementation ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md).

This is the current coding-feature sequence. IDs **C1–C7** distinguish it from
the earlier M1–M5 team-foundation milestones and their completed slices. Do not
rebuild approved types, inboxes, approval modes, retirement or the agent loop.
Extend the same primitives through the remaining deployment adapters.

## Defaults and implementation rules

- Default entry point: a single coding agent, with no automatically started children.
- Default team preset: shared sandbox, separate builder worktrees.
- Each child's worktree/checkout is its working directory and ordinary file-tool
  scope. Shared Git metadata access is deliberate and narrowly validated; cwd is
  not kernel containment for shell commands.
- Isolated workers have independent checkouts and their own Git metadata.
- Default isolated handoff: artifacts. GitHub publication is opt-in; when enabled,
  prefer the trusted publishing tool. Sandbox CLI credentials are an explicit mode.
- Presets are versioned configuration over the existing runtime. Keep optional
  publishing integrations out of the core dependency path where possible.
- All choices stay within application-owned delegation and provider enforcement.
  Unsupported restrictions or modes reject instead of falling back.

## Milestones

| ID | Deliverable | Dependency | Status |
| --- | --- | --- | --- |
| C1 | Child directories and Git worktrees inside the shared sandbox | Existing local worktree receipts and shared service teams | Verified |
| C2 | Single coding-agent and shared-team presets | C1 | Verified |
| C3 | Delegated child sandbox placement and lifecycle | Existing type/delegation/lifecycle contracts | Verified |
| C4 | Isolated coding-team preset and artifact handoff | C2, C3 | In progress |
| C5 | Harness-managed GitHub checkout/publication integration | C4 handoff contract | Pending |
| C6 | Explicit sandbox Git CLI credential mode | C3, C5 policy/credential contracts | Pending |
| C7 | Combined parallel, mixed-placement and recovery qualification | C1–C6 | Pending |

Execute in this numbered order. C3 builds on generic team placement rather than
adding a coding-only sandbox provisioner. Each milestone includes tests and ledger
updates; C7 expands coverage across the combined system rather than postponing
verification until the end.

### C1 — Child directories and Git worktrees inside the shared sandbox

Extend the tested local directory/worktree preparation path through the service
and OpenShell ToolRunner. The trusted adapter prepares a distinct child worktree
at a pinned repository/base revision and persists the selected working directory
and receipt. The execution environment keeps its original identity and enforcement
root; the child's cwd and file-tool scope must not be confused with that root.

Record the worktree's repository/common-metadata relationship and original path
identities. Adapt trusted Git inspection to admit that recorded relationship
without broadening ordinary file-tool access. A lost preparation response permits
inspection of the original operation, not another `git worktree add`.

Acceptance:

- Two builders edit separate worktrees in one sandbox; their relative paths and
  shell cwd resolve to their own directories.
- File tools reject sibling writes, traversal and symlink escapes. Authorized Git
  operations work with the registered shared metadata; replaced/arbitrary metadata
  pointers reject. Shell access remains bounded by the actual shared sandbox.
- The lead can inspect builder commits; a restart or lost acknowledgement preserves
  the original directory, branch and environment without replaying preparation.
- Existing same-directory service sessions retain their supported behavior.

### C2 — Single coding-agent and shared-team presets

Provide versioned SDK presets with customizable prompts, tools, models, budgets
and delegation. Start the single agent without children. With delegation enabled,
it may create helpers through the same approved-type/team mechanisms. The shared
team preset supplies lead, builder and verifier roles and uses C1 worktrees.

Finalize the small typed construction API in Rust and TypeScript against executable
examples. Model defaults remain overridable per session; reviewers stay separately
configured. Credentials remain outside preset bundles. No separate agent loop.

Acceptance:

- A single agent builds a small web app; optional helper creation respects the
  supplied ceiling and configured approval mode.
- A lead coordinates builders, a verifier checks the exact submitted commit, and
  explicit integration handles success and conflicts.
- Rust in-process/managed and TypeScript managed/service entry points expose the
  supported preset behavior with storage parity where those modes apply.
- Spawning never implicitly commits, merges, pushes or grants GitHub access.

### C3 — Delegated child sandbox placement and lifecycle

Implement Team/Member/New selection through the existing trusted execution-provider
adapter. New environments use app-authorized profiles and resolved restrictions.
Every requested file/network/credential/resource restriction must be supported and
enforced by the selected provider; otherwise reject it. Definitions cannot enlarge
the lead's delegation ceiling. Sharing requires compatible resolved enforcement,
not matching labels or narrower tool lists alone.

Persist provisioning intent and original provider/storage identities before I/O.
Reconcile unknown outcomes through reads of the original resource. Track ownership,
references, quotas and retention so retirement cannot delete an environment still
in use or silently destroy retained work. Do not claim independently persistent
storage when the provider only preserves files for the lifetime of its sandbox.

Acceptance:

- Shared, reused-member and new isolated placements behave as requested.
- Excess grants/resources, incompatible sharing and unsupported constraints reject.
- Controller failure, lost create/delete replies, cancellation and retirement do
  not create replacements, leak unaccounted capacity or delete shared resources.
- Original provider identity, access and lifecycle remain visible through durable
  state/events. Keep the harness/provisioner outside OpenShell for this sequence.

### C4 — Isolated coding-team preset and artifact handoff

[Implementation details and build order](CODING-HANDOFF-IMPLEMENTATION.md).

Stage an independent checkout/source snapshot at a recorded base revision in each
worker environment. Do not mount a linked worktree dependent on the lead's Git
metadata. Implement one durable handoff record for repository/base, submitted
revision, artifact digest/reference and verification evidence. Use existing artifact
storage and authorized metadata APIs rather than a second untracked transfer path.

Builders export patches or Git bundles. Verifiers inspect the exact submission;
the lead explicitly applies/integrates it and handles a changed integration base.
Validate paths and object/artifact identity. Importing/publishing data must not
execute repository hooks or arbitrary code; running project tests is a separate,
policy-governed execution step.

Acceptance:

- Builder → verifier → lead works without sharing a filesystem or GitHub write key.
- New/deleted/renamed/binary files, stale bases, conflicting edits and invalid
  artifacts have explicit outcomes.
- Handoffs survive process loss and duplicate delivery without blindly applying
  changes twice or reporting unverified work as integrated.

### C5 — Harness-managed GitHub publishing

Add optional trusted GitHub integration for private-repository staging and branch
publication. Read access and write access are separate. Keep credentials in the
trusted harness/publishing service; the child submits an artifact and constrained
publication request. Reuse C4's revision/evidence contract.

Enforce stored connection references, permitted repositories, branch prefixes and
operations independently of model input. Pin the expected remote state. Publication
does not implicitly authorize force-push, PR creation, merge or administration.
Return durable operation status and the published commit/branch identity. Lost
responses remain unknown until the original remote state is inspected; no automatic
mutation retry, alternate repository or fallback credential mode.

Acceptance:

- Qualify locally against a controlled Git remote and deterministic API fixtures:
  allowed publication, denied scope, existing-branch conflict, response loss and
  credential expiry/revocation.
- Credentials never enter child tool results, Git URLs or observation payloads.
- A live GitHub write test requires a separately authorized disposable repository;
  it is not implied by approval to run local Docker/Kubernetes qualification.

### C6 — Explicit sandbox Git CLI access

Add the opt-in CLI mode using provider-supported credential-helper/secret delivery.
Keep private checkout and publishing permissions separate, avoid persistent tokens
in Git configuration/URLs, and honor expiry, refresh and revocation. State clearly
that a credential delivered into a shared sandbox is potentially accessible to
other processes in that sandbox. Never switch to this mode after a publisher error.

Acceptance:

- Authorized CLI fetch/push works against a controlled repository; denied or expired
  access fails clearly, and unsupported delivery cannot broaden host access.
- Logs, events, configuration and Git remotes do not expose tokens. Credential
  cleanup and shared-versus-isolated scope are verified.

### C7 — Combined resilience and handoff qualification

Run all three presets, then two concurrent teams with multiple builders/verifiers
and shared/isolated/mixed placements. Combine deterministic failure barriers with
bounded live-model work using an authorized credential source. Exercise restarts,
worker replacement, parked approvals, cancellation, budgets, quotas, workspace
retention, handoff conflicts, unknown publication outcomes and cleanup.

Keep repeatable fast tests, real database/process tests and local Kubernetes/
OpenShell scenarios. Expose useful lifecycle/handoff identifiers through events
and existing observability. Produce grouped test-case documentation and concise
developer examples. Record actual platform/provider/storage qualification instead
of inferring it from compilation or mock results.

Acceptance: each supported variant works end to end, existing SDK/service suites
pass, no unowned resources are changed, and every remaining limitation is documented.
Cloud deployment trials follow this sequence under their own budget/authorization.

## Progress ledger

| Milestone | Implementation/evidence | Result |
| --- | --- | --- |
| Planning | Linked design, current team ledger and this sequence; retained existing completed foundation. | Approved; no runtime changes in the planning slice. |

For each verified slice, append the source commit, meaningful tests, local fixture
cleanup, remaining gaps and merge commit. Earlier foundation evidence remains in
the linked team ledger; this plan does not count those checks as newly rerun.

### Capacity preflight — October 31

[Read-only capacity evidence](evidence/coding-presets-capacity-20261031.json):
approximately 35 GiB free on the Mac; Docker's sparse disk has virtual headroom
but remains constrained by that physical free space. The Rust target directory
occupies approximately 85 GiB (84 GiB debug); a safe reclaimable subset has not
yet been inventoried. No files, workloads or volumes were removed.

The existing kind node is Ready without disk/memory pressure. Its Docker VM has
approximately 8 GiB memory, with approximately 2.7 GiB available at inspection.
Start C1/C2 with bounded shared-sandbox tests and reuse pinned images. Before the
full isolated/parallel matrix, recommend another 20–30 GiB free and approximately
16 GiB Docker memory, or appropriately staged smaller batches. The Mac has 96 GiB
physical RAM. Changing Docker allocation/restarting it is not performed by this
check; existing demos remain running. Recheck actual capacity and CPU/memory
requests before peak tests. These are working allowances, not measured peak usage.

### Targeted build-cache cleanup — October 31

Removed only this harness worktree's `harness/target/debug/incremental` after
acquiring the existing Cargo debug lock and confirming no active Cargo/compiler
processes. Cargo metadata points to this target; no other workspace target aliases
or configured target-dir overrides were found in the inspected locations. These
are harness build outputs; individual historical artifacts are not agent-labelled.

The directory reported approximately 39 GiB, and actual host free space increased
by approximately **25.7 GiB**, from 34.7 to **60.4 GiB**. Preserved debug executables,
dependency/test artifacts, release builds, source, other worktrees, Cargo downloads
and Docker/Kubernetes resources. No further cleanup is needed before C1.

The follow-up daemon read still reports approximately 8 GiB RAM; the user's intended
16 GiB increase is not yet confirmed. Recheck after it applies, before large
isolated-sandbox batches. Bound test concurrency and monitor incremental-cache
growth; use `CARGO_INCREMENTAL=0` for sustained qualification where practical.

### C1 journal — October 31, after Docker restart

Docker now reports 16,745,295,872 bytes (approximately 15.6 GiB, configured as
16 GiB); host free disk is approximately 60–61 GiB. Kubernetes and both OpenShell
gateways recovered. One retained observability demo API has an HTTP 401 readiness
probe; this is separate from the new task-owned fixtures. No demo credential was
created or changed. The restart removed the previous disposable database; created
a new loopback-only `ow-coding-presets-pg-20261031` fixture for this sequence.

Feature branch: `feat/coding-shared-workspaces-20261031`. Implementing controller
preparation intents/leases, original-environment ToolRunner requests, session-bound
workspace receipts and narrow Git metadata validation. Receipts now pin the
per-worktree metadata pointer/identity as well as common metadata. Ordinary file
tools retain the child's cwd scope. Initial coding regressions passed; two new
fast-test fixture issues were found (macOS process support and `/var` symlink
canonicalization) and corrected. Full reruns and real OpenShell qualification
are pending; this is not a completion claim.

### C1 verified — October 31

Source: `da719549`. [Evidence](evidence/coding-presets-c1-20261031.json) and
[grouped test catalog](CODING-AGENT-PRESETS-TESTING.md). Final real OpenShell run
passed in **204.63 seconds**, including assertions on actual tool exit codes and
stdout: two scoped builder worktrees/commits, harness-level traversal denial,
exact-submission verifier, lead inspection, competing controllers, worker
replacement and TypeScript reconnect/shared-verifier/separate-environment checks.
Provider deletion confirmed removal of the original resources; fixture execution
and recording schemas were dropped. No live LLM, GitHub write or cloud operation.

Also passed: 21 Linux coding/workspace tests (one separate fixture entry ignored),
81 macOS runtime/coding/team tests, six PostgreSQL service tests, four SDK
workspace modes (SQLite/PostgreSQL × embedded/managed, 178.60 seconds), nine
TypeScript service tests, strict all-target Clippy, formatting and diff checks.
The first full OpenShell attempt exposed a test assertion expecting a tool error
where the harness had correctly denied dispatch; corrected it and strengthened
command-result assertions. The initial TypeScript HTTP run lacked tool-sandbox
loopback permissions; its permitted rerun passed all nine cases.

Preserved the original shared sandbox identity and enforcement root. First-party
coding tools validate the pinned per-worktree Git pointer/common metadata while
ordinary file tools stay in the child's directory. Shell cwd is not containment.
The previous unpinned worktree receipts cannot silently gain this Git exception;
newly prepared receipts carry the required identities. C2 presets and isolated
placement remain separate work. Host free disk after qualification is about
58 GiB; incremental compilation was disabled. Temporary TLS/runner/forward and
the disposable PostgreSQL remain available only while the next local milestone
needs them, then will be removed; retained demos were not changed.

### C2 journal — October 31

C1 merged/pushed as `40c41898`. Continuing on
`feat/coding-presets-c2-20261031`. Added versioned coding definitions (one
canonical configuration, generated TypeScript copy), standard Agent customization,
and service-release conversion with explicit RuntimeProfile compatibility checks.
Shared-team builder worktrees are resolved by the runtime before reservation,
using an app-pinned base SHA and team/instance-derived paths/branches; this is not
left to prompt compliance. Verifiers select the submitted builder directory
explicitly. The lead integrates in the supplied repository's existing main
worktree; no implicit branch switch, commit, merge, push or credential grant.
Implementation/qualification remains in progress.

### C2 verified — October 31

Implementation `c00b1f40`; identifier validation follow-up `e701a5b0`.
[Developer guide](CODING-AGENTS.md), [evidence](evidence/coding-presets-c2-20261031.json)
and the linked test catalog describe the shipped APIs and limits.

Passed five Rust preset tests, including SQLite/PostgreSQL × embedded/managed
workflows (260.04 seconds), two actual TypeScript stdio/storage workflows with
streamed events and restart (117.62 seconds), three TypeScript preset/compatibility
cases, nine TypeScript HTTP/service cases, 14 SQLite and 13 PostgreSQL team
contracts, and 40 workspace/briefing/coordination/definition/catalog regressions.
Final OpenShell service qualification passed in **202.13 seconds**: preset-derived
releases and automatic scoped builder worktrees, plus a real successful integration
and conflicting cherry-pick with explicit abort and preserved submitted HEAD.
Original sandbox deletion and fixture schema cleanup completed. The final
identifier-only tightening was checked separately by both preset unit suites;
strict all-target Clippy and formatting/diff checks passed.

Qualification found and fixed a missing SDK-ingress allowlist entry for the new
trusted option. Kept the existing same-directory spawn response shape; selected
workspaces add their resolved selection. Public snapshots continue to hide private
workspace receipts. Additional checks prove working/reviewer model separation
in service conversion. No key, provider, bypass mode or worker is implied by
constructing a preset. One canonical JSON definition ships in Rust; TypeScript's
generated configuration is checked against it.

The service test deliberately reused the C1 runner image: C2 changes trusted
coordination/defaults, not its workspace/MCP protocol. The current host service
and SDK were built from the C2 source. Unsupported service-local policies and
capabilities reject during conversion. The source coding bundle enables planning;
verifier shell access is explicitly not read-only enforcement. Remaining isolated
placement, source staging, publication and credential work stays in C3–C7.

### C3 journal — October 31, contract slice

[Placement implementation flow and initial provider scope](TEAM-PLACEMENT-IMPLEMENTATION.md).

C2 merged/pushed as `08c812bb`. Continuing on
`feat/team-placement-c3-20261031`. Adding explicit per-instance sandbox selection,
pinning the resolved choice with the original reservation, and checking custom
type settings against the approved envelope as well as the app ceiling. A retry
uses its original type revision/default, not a newly approved revision. Shared
worktree defaults do not apply to a new independent environment.

A trusted adapter capability gate stays disabled in existing SDK/service adapters
until provider provisioning, ownership/reference/quota handling and recovery are
qualified. Adding request fields does not silently enable provisioning. Provider
resolution must reject unsupported grants and preserve original identities.
The separate sandbox needs its own source checkout/handoff (C4); no linked Git
metadata is implicitly mounted across sandboxes. Implementation/tests ongoing.

C3 contract qualification (in progress): shared SQLite/PostgreSQL reservation
contract passed, including per-instance New/Member choices, excess resource/grant
rejection and unchanged reservation identities. Unit cases pin the original
custom revision/default after a newer type revision is approved and reject
instance settings outside that original envelope. Existing adapters explicitly
reject unbound non-Team choices at both preparation and admission rather than
running them in the lead/host environment. The TypeScript serializer rejects
unknown settings instead of silently dropping them. No New/Member provisioning
is enabled yet; provider binding/lifecycle remains the next C3 slice.

### C3 journal — provider outbox contract, October 31

Added a private PostgreSQL outbox for per-instance New placement. Reservation
atomically records the original environment/spec/profile, principal/project,
quota ownership and durable dispatch marker before provider I/O. Immutable
RuntimeProfile IDs and exact configured CPU/memory are required; unsupported
file/network/credential grants and foreign deployment identities reject.
Child and source releases must permit the target profile. Existing admission
quotas count pending and unknown team environments alongside ordinary sessions.

Recovery uses a fenced claim: only its first dispatch may create; subsequent
claims inspect the original identity. A stale/expired claim cannot update either
the environment binding or the outbox. The shared environment observation logic
now participates in that same transaction. Matched lead/team/outbox lock ordering
to avoid a claim-versus-reservation deadlock. Revocation/retirement before the
first dispatch cancels only a proven-unstarted allocation; uncertainty after
dispatch retains ownership/quota and allows original-resource inspection.

Five new PostgreSQL contract tests and all 21 server-library cases passed in
1.51 seconds. They cover lost-create replies, concurrent controllers/quota,
changed/foreign identity, stale receipts, pause/unpause, credential revocation,
retirement and quota preservation after unknown outcomes. One initial assertion
used a reopened store without claiming the lead lease; corrected that fixture
read to use its original owning store. These use synthetic provider observations;
OpenShell creation and public placement admission remain gated pending the next
C3 binding/controller/cleanup qualification. C3 is not complete or merged.

### C3 journal — binding/controller integration in qualification

Connected the outbox to the trusted OpenShell controller. New and Member bindings
are pinned before workspace preparation and child admission; workspace markers,
runner roots and private runtime factories follow the selected environment's
actual profile. The model sees only choices within the configured delegation.
Service configuration rejects unsupported grants, mutable profile aliases,
resource expansion and changed approval/question behavior. Local sandbox-off SDK
adapters still do not provision external sandboxes.

Added pending/active placement references to deletion checks and extended original
resource cleanup to team-created environments. Bound members retain their own
reference when their source retires. Unknown provider outcomes produce durable
activity plus a lead inbox notice; normal readiness is activity, without an extra
wake that could distract the lead from its current task. Sanitized storage
unavailability now has a type, so controller failures cannot be mistaken for a
permanent denial.

All 22 server-library tests passed (1.48 seconds), including normal service
configuration and member reuse/retirement. TypeScript placement serialization
(two tests) and all nine HTTP/service tests pass. The first physical OpenShell
attempt cleaned up after a test read occurred before child admission: a completed
lead wait can mean inbox wake, not child completion. The rerun checks durable
member completion and also injects a real lost child-create reply before
restarting competing controllers. Physical qualification is ongoing; C3 remains
unmerged pending its final checks.

The physical rerun exposed an actual PostgreSQL deadlock during the existing
worker-restart case. The database lock graph showed an API submission holding
the global security-audit append lock while waiting for the lead session, with
child admission holding that lead while appending its creation audit. Moved
submission, control and artifact audit appends after their data/row locks, keeping
receipt/event/audit atomicity and commit-ordered audit cursors. Added a
barrier-based test that holds the lead row, starts a blocked authenticated
submission and proves the trusted admission can still obtain its audit lock.
Also added sanitized storage-outage classification and post-binding credential
revocation/retained-resource cleanup cases. These are being rerun before the next
physical qualification. No transient operation is blindly replayed to hide errors.

The deterministic audit-lock regression and all 25 server-library cases passed
(1.70 seconds), followed by 62 PostgreSQL regressions across seven suites,
49 core team/definition/catalog cases plus eight real filesystem/worktree cases,
two TypeScript placement cases and nine HTTP/SSE cases. Strict all-target Clippy
passed. The following physical attempt reached the child workspace path and
correctly refused a missing selected directory: directory selection means use an
existing directory, not create it. Corrected the fixture to let the new builder
write `build/proof.txt`, then bind a private verifier to that existing directory.
The adapter now prepares that verifier's cwd in the chosen child environment;
C4 remains responsible for staging independent source checkouts. Original
sandbox cleanup completed after both failed physical attempts.

Physical C3 checks now pass through original-UID recovery, two independent
smaller sandboxes and a private verifier reading the builder's selected directory.
Kubernetes reports 1 CPU/768 MiB for the lead and 1 CPU/512 MiB for each new child.
The combined fixture then correctly hit its aggregate default team token budget
before the older shared-builder commit step. The scripted model intentionally
retains conservative output reservations. Set an explicit bounded test-only team
budget for this expanded scenario; production defaults and limit handling are
unchanged. Rerunning the full sequence, including cleanup, before marking C3
verified.


### C3 verified — October 31

Implementation `e63e65b1`, following gated contract `e9b6049d` and outbox contract
`fc3ba019`. [Evidence](evidence/coding-presets-c3-20261031.json),
[implementation](TEAM-PLACEMENT-IMPLEMENTATION.md),
[developer usage](CODING-AGENTS.md#choose-a-childs-sandbox-in-the-openshell-service)
and the [grouped test catalog](CODING-AGENT-PRESETS-TESTING.md) describe the result.

Final complete OpenShell qualification passed in **328.61 seconds**, after the
preceding 315.39-second success. This final run includes the retirement-recovery
follow-up and profile schema guidance. Two instances of one builder type run in
independent 1 CPU/512 MiB sandboxes; the shared profile uses 1 CPU/768 MiB. A real
child create loses its acknowledgement, competing restarted controllers inspect
the original UID, and a private verifier selects the builder's existing directory.
Lead/other-child file absence proves the physical separation. Shared worktrees,
exact-commit verification, successful integration and explicit conflict/abort
remain covered. Rust and TypeScript SDKs execute actual OpenShell tools.

Passed 25 server-library cases, 62 PostgreSQL regressions, 49 core team cases,
eight real Git/workspace cases, the final seven-case schema/briefing follow-up,
two TypeScript placement cases, nine HTTP/SSE cases, strict all-target Clippy,
format/diff checks and six-document link validation. The audit/session deadlock
is covered by a deterministic lock barrier; audit/receipt rollback remains intact.
Post-binding revocation preserves work without admitting a child. Pending/member
references prevent deletion; retirement can reconcile after session closure
committed before a crash. Storage outages retain a sanitized infrastructure type.

Cleanup confirmed original sandbox deletion and removal of both test schemas;
only the retained gateway remains in its namespace. The disposable PostgreSQL,
private TLS files, matching runner and loopback forward remain available for C4.
No cloud provisioning, live model calls or GitHub writes were used. Host free disk
is about 56 GiB with incremental Rust compilation disabled. New/Member physical
placement is qualified for the OpenShell/PostgreSQL service. Local sandbox-off
SDKs still reject unsupported placement. Source staging and isolated preset
handoff remain C4; publication and CLI credentials remain C5/C6.


### C4 journal — starting after C3 merge

C3 merged and pushed to the canonical public feature branch as `f8b31eea`.
Continuing on `feat/coding-handoff-c4-20261031` with the approved artifact-first
handoff. [C4 build details](CODING-HANDOFF-IMPLEMENTATION.md) separate portable
manifest/Git validation, storage/controller recovery and final physical workflow.
Start with bounded real Git bundles and exact repository/base/submitted identity;
no GitHub token, network fetch or source mount is implied. Source setup and
model-requested exports must retain distinct provenance. Artifact-store
credentials stay in the trusted harness. Qualification fixtures remain available,
with about 56 GiB free and incremental compilation disabled.

### C4 journal — portable manifest and Git bundle foundation

Added a portable manifest binding repository identity, producer, exact base and
submitted revisions, object format and artifact reference/digest. Source snapshots
pin one revision; collaborator reports bind that submitted revision and cannot
supply a verified/approved flag. Import intent pins the original manifest and a
portable relative destination. Validation is explicitly not authorization or proof
of published artifact bytes.

Implemented bounded local Git bundle export/inspection and full object validation.
A private namespaced ref pins one exact commit; branch tips do not move and no
`--all` export occurs. The operation marker commits before that metadata write.
Re-entry inspects its original complete bundle/ref/receipt and never re-exports
an incomplete or missing result. Git runs with cleared environment, disabled
hooks/helpers/network/lazy fetching/replacement objects. Parent and pack/index
children share a guarded process group so cancellation/limits cannot leave a
pack helper intentionally running. Streamed output is capped at 64 MiB.

Validation uses a fresh private bare repository, one advertised ref, strict object
checks and exact commit ancestry. Incremental missing prerequisites and even
unadvertised unreachable objects reject. Bytes remain data; no project checkout,
tests, artifact publication, network fetch or model tool is enabled by this slice.

Four portable contract tests and seven real Git cases pass (3.62 seconds for the
Git suite), including SHA-1/SHA-256 independent repository roundtrips, binary/new/
renamed/deleted files, replaced roots/refs, corrupt bytes, marker symlinks,
repository helpers/alternates/replacements and a real incompressible 65 MiB export
stopped at the 64 MiB limit without a receipt or automatic replay. The preceding
five coding/search/patch and eight workspace regressions also pass. Tests found
and corrected cross-platform path acceptance and a fixture using clone's branch
selection for a deliberately private bundle ref; receivers fetch that exact ref
explicitly. Artifact setup provenance, storage/controller transfer, independent
checkout/defaults and physical OpenShell handoff remain the next C4 slices.

C4 foundation strict all-target runtime Clippy, formatting and diff checks passed.
The remaining storage/controller and physical handoff gates remain closed.

### C4 journal — artifact provenance contract and admission gate

Artifact registrations now distinguish ordinary turn outputs from trusted team
setup snapshots. Ordinary registrations omit the default origin when serialized,
so existing immutable receipts retain their JSON identity. Setup snapshots have
an explicit team/setup identity and no invented LLM turn ID. Git bundle content
uses `application/x-git-bundle`. Public registration requests cannot select setup
origin, and PostgreSQL rejects even a well-formed setup label until the trusted
source-intent catalog authorizes that exact original operation. This gate remains
closed; a label is not a permission receipt.

Six artifact-store tests, four real PostgreSQL artifact cases and the public
request deserialization test pass. They cover existing receipt compatibility,
streaming/restart/conflict behavior, setup-label rejection without publication or
connection poisoning, and rejection of extra credential fields. No setup upload
endpoint or model tool is enabled by this contract checkpoint. Host free disk
remains about 56 GiB. Continuing with bounded private chunks and independent
checkout mechanics before wiring source admission and the service controller.

### C4 journal — private chunks and independent checkout mechanics

Extended the existing trusted `workspace-stdio` channel with private handoff
methods, not discoverable model tools. Frames remain bounded at 64 KiB and data
chunks at 32 KiB. Transfers bind the original manifest, export receipt, scope and
root identity before accepting bytes. Immutable chunks may be acknowledged again
only when their offset and bytes match; holes and changed data reject. Reads
require the original completed export receipt and original repository identities.
Artifact-backend digest verification remains required before publication.

A receiver validates the complete bundle and exact commits before checkout. It
creates independent Git metadata without remote access, templates, hooks, filters
or recursive submodules. Checkout dispatch commits before the filesystem mutation;
a lost reply permits receipt inspection only. Missing evidence, replaced metadata,
a changed pre-acknowledgement HEAD or an existing destination never triggers a
reset, replacement or replay. Reserved `.openworker` paths cannot be destinations.
A separate 128 MiB expanded-tree / 10,000-entry bound prevents a small compressed
bundle from materializing an unlimited working tree. A failed/unknown checkout
remains explicit rather than deleting its original directory.

Qualification: four manifest cases, thirteen real Git/private-stdio cases, eight
workspace regressions and five coding/search/patch regressions pass. The Git
suite takes 4.13 seconds and includes SHA-1/SHA-256, actual subprocess chunk
transport, inspection after lost acknowledgement, corrupt bytes, immutable chunk
replay, wrong scope, symlink markers and an oversized compressible checkout.
Strict all-target Clippy passes for runtime, artifacts, PostgreSQL and the service
(with its OpenShell test feature); format and diff checks pass. PostgreSQL setup
admission remains gated pending the durable source-intent controller. These tests
are local process/filesystem tests, not yet the C4 physical OpenShell workflow.

### C4 journal — durable source setup and catalog publication

Added a PostgreSQL source-setup outbox keyed to the original team/lead, activated
release/profile digests, environment, pinned repository revision and configured
artifact-store binding. Application setup authorizes bounded source bookkeeping;
it does not manufacture a tool approval or LLM turn. The original credential is
rechecked before export claims, source reads and artifact registration/publication.
One controller gets first export dispatch. Reclaimed work inspects the same
operation; stale claims cannot record or publish a replacement. Immutable artifact
uploads can resume against the same original ID/bytes. An uncertain transfer
retains its bundle and artifact identity. Authority loss blocks work; restoring a
profile still needs an explicit application retry and never resets dispatch.

The private catalog path now admits setup provenance only with that live original
source claim, exact exported byte/digest receipt and original store binding. Public
registration/upload and generic host publication cannot manufacture setup authority.
Setup artifact events use SQL NULL turn IDs rather than invented/empty LLM turns.
Existing ordinary-turn artifact validation and quotas remain in use.

Trusted source staging streams bounded ToolRunner chunks into the existing artifact
backend; no storage URL/key/credential enters the sandbox. Transfer has its own
bounded controller task so a slow export/upload cannot block cancellation or
cleanup reconciliation. No second model/agent loop was introduced.

All 30 service-library tests pass, including four new real PostgreSQL source cases
(1.65 seconds for the full library suite). The tests distinguish synthetic catalog
receipts from Git integrity. They cover lease expiry, stale owners, changed source
or artifact binding, original-credential revocation, same-ID upload recovery and
explicit reauthorization without replay. A test-fixture synchronous PostgreSQL call
initially ran directly on a Tokio worker; it now uses the existing blocking bridge.
Strict all-target Clippy passes. The matching offline Linux runner build passes
38 executed runtime/Git/workspace/process tests (one fixture-only test ignored).

Preparing physical OpenShell qualification for this source/receiver slice. The
isolated preset/default configuration, durable child import admission and
model-requested submission/integration APIs remain C4 work; source staging alone
is not the complete isolated coding-team feature. Canonical stays at verified C3
until the full C4 milestone is qualified.

### C4 journal — first physical source-staging attempt

The expanded OpenShell run passed existing private-type, restart, isolated-placement
and shared-worktree checks, then timed out waiting for the new source artifact
(310.09 seconds total). Source setup retained its original export intent and did
not create a replacement. Investigation found that the new controller used a
prefixed claim ID as an attachment launch ID; the pinned OpenShell transport
requires a non-nil UUID before opening the connection. Source claims and explicit
receiver-test attachments now use UUIDs, with a storage regression assertion.
The four source tests pass again (0.79 seconds).

Cleanup confirmed only the retained gateway remains and no service/recording test
schemas remain. This was a failed qualification, not C4 completion. The matching
Linux runner already passed 38 tests and is available under the verified local
OCI index `sha256:bb4c0555152bed2c2bf43428a2cd147449aa9220720d56e19cc765ef1378ef2e`;
its runtime code is unchanged by this trusted-controller-only UUID correction.
Rerun the fresh physical workflow before recording successful source/receiver
qualification or merging any C4 milestone.

### C4 journal — physical source publication and namespace boundary

The UUID-corrected run successfully exported and published the real pinned source
artifact, then stopped at the test's direct HTTP download (255.03 seconds). The
raw test client omitted the required `x-openworker-namespace` header that the SDK
normally supplies, and the service correctly returned 403. Corrected that test
request to supply the existing namespace/service identity headers; no authorization
check was loosened. Independent receiver qualification still requires a complete
successful run. The source/default contract for the next C4 slice is being drafted
separately while this runtime/runner protocol remains frozen for qualification.

### C4 journal — isolated source/default contract

Added the shared configuration contract for the next admission slice. Prebuilt
builders default to the initial snapshot; verifiers require a submitted handoff
reference. Independent default paths include both team and instance IDs. Explicit
relative paths remain possible, while reserved harness paths, host paths and
linked-worktree/source combinations reject. Dynamic types do not silently inherit
prebuilt defaults. Three new contract cases pass, alongside the four manifest
cases. Source configuration now uses this same runtime DTO in PostgreSQL without
changing its serialized fields or validation; all four source catalog tests pass
again (0.91 seconds), and runtime/PostgreSQL all-target Clippy passes.

These are data/resolution contracts. Provider/profile authority, atomic team
configuration and durable child-import admission remain the next C4 integration
step. The SDK isolated preset is not yet exposed. The full physical source/receiver
run uses the prior committed service/runner behavior; record its exact source
identity separately from this additional contract slice.

### C4 journal — source and separate receiver physical qualification passed

The complete local service/OpenShell suite passed in **357.30 seconds** against
service-test source `3564b027` and the digest-pinned C4 Linux runner. It proved
trusted source export, authorized immutable artifact publication/download, and
independent Git checkouts in two distinct OpenShell sandboxes. Original checkout
receipts were inspected without repeating checkout. Existing two-API/controller,
worker-restart, separate-profile placement, shared-worktree/conflict and Rust/
TypeScript service cases also passed. The raw HTTP namespace rejection remained
enforced; only the test request was corrected.

[Slice evidence](evidence/coding-handoff-source-c4-20261031.json) records hashes,
OCI identity, pod resource metadata, test counts and both preceding failures.
The separate source/default contract checkpoint is `8c33cf25`; its unchanged
source DTO and three new resolution cases are distinguished from the physical
service commit. The seven-suite PostgreSQL regression totals **63 passes**.
Six linked implementation/reviewer documents have no missing local links.

This qualifies source staging and private receiver mechanics, not the whole C4
preset. Next: bind source selection/defaults atomically at reservation, persist
child-import intent and Git metadata identity through admission, then expose
submission/report/reception and isolated SDK helpers. Canonical remains verified
C3 until the complete C4 workflow passes. Local disposable fixtures can be reused;
free host disk is about 55 GiB. No paid model/cloud/GitHub action was used.

### C4 journal — imported Git identity through coding-tool launch

The private receiver now converts only its original acknowledged import into a
managed-workspace receipt with an independent Git-directory identity. Coding tools
verify it before each call. A child may advance its own commits, while replacement
Git directories, external common/object stores, symlinked Git storage and mixed
linked-worktree exceptions reject. Existing C1 receipts omit the optional field,
preserving their serialized identity. The private conversion pins the original
operation, scope and destination rather than adopting an existing directory.

Added two real filesystem cases and extended the subprocess protocol roundtrip
through `handoff/workspace` and `workspace/verify`. The first extended roundtrip
found inconsistent `/var` versus `/private/var` roots on macOS; the private server
now canonicalizes its root once for all methods. All 15 Git/transfer tests, eight
workspace cases, five coding/search/patch cases and three default-resolution cases
pass on macOS. Strict all-target runtime/artifacts/PostgreSQL/service Clippy passes.
The newer offline Linux runner also passes **43 executed tests** across six suites
(one fixture-only test ignored), including real shell/process cleanup.

This newer private conversion/receipt is locally qualified, not included in the
preceding physical source/receiver image. A matching new runner is available at
`/private/tmp/ow-coding-admission-runner-20261031/openworker` for the next import/
admission cluster slice. Preserve the earlier source evidence and image identity;
do not claim the complete isolated preset or child-import controller is finished.
