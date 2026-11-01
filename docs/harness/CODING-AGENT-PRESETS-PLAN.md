# Coding agent presets: implementation sequence and progress

Status: C1–C7 locally verified; see platform and retained-cleanup scope below. Canonical feature branch: `openworker-harness-sdk`. Use a feature
branch for each slice and merge only verified changes. No public-main merge or
cloud provisioning is part of this plan.

Design: [coding presets, defaults and publication](CODING-AGENT-PRESETS.md).
Illustrated options: [workspaces, source transfer and GitHub access](CODING-WORKSPACE-SCENARIOS.md).
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
| C4 | Isolated coding-team preset and artifact handoff | C2, C3 | Verified: complete local OpenShell/PG17 run, November 1 |
| C5 | Harness-managed GitHub checkout/publication integration | C4 handoff contract | Verified locally: controlled Git and authenticated TLS; no live GitHub write |
| C6 | Explicit sandbox Git CLI credential mode | C3, C5 policy/credential contracts | Verified: named grants, SDKs and local OpenShell |
| C7 | Combined parallel, mixed-placement and recovery qualification | C1–C6 | Verified locally: parallel teams, live model, full regression and ownership cleanup |

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

Add the opt-in CLI mode. For OpenShell, use its endpoint-bound credential provider
and placeholder mechanism first: the real token stays in OpenShell's trusted path,
while Git uses the supplied opaque reference over inspected HTTPS. Qualify allowed
binaries/requests and credential bindings independently. Other credential-helper or
raw-secret delivery modes require explicit provider-specific support; no fallback.
Keep private checkout and publishing permissions separate, avoid real tokens in Git
configuration/URLs, and honor expiry, refresh and revocation. A placeholder still
confers capability within its sandbox; it is not agent-private in a shared sandbox.
Never switch to this mode after a publisher error.

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

### C4 journal — storage-neutral reservations and atomic source configuration

Coding source/default configuration now resolves before child reservation and is
an immutable retry input. The same domain contract runs on SQLite and PostgreSQL:
builders get stable independent paths/initial source; verifiers must name a
submission; source choice is stored with the original instance. Source imports
need an explicit qualified-adapter capability. Ordinary API commands cannot record
source acknowledgement. A generic directory receipt alone cannot make a
source-selected child ready; it also needs the matching manifest, independent Git
identity and managed-workspace binding. Private source receipts stay out of public
snapshots. Existing requests omit the optional source field.

PostgreSQL team creation can now record configuration, templates, lead membership
and initial source intent in the same transaction using the server-supplied
artifact binding. The old separate source-setup adapter shares that transaction
logic. An injected audit failure proves all four records and source events roll
back together. The fixture reconnects and reads durable state after the deliberately
poisoned bridge; it never steals the model worker lease to inspect setup.

Generic workspace preparation excludes source-selected children. Provisioning waits
for source acknowledgement, and an unavailable child template closes only its
unstarted reservation instead of stranding it behind missing source/placement
receipts. Unknown export/upload and authority loss now produce deduplicated,
durable notices in the lead inbox. They identify original retained work and do not
claim completion or authorize retries. Explicit application reauthorization keeps
the original dispatch bit.

Verification so far: 16 SQLite team cases; 15 PostgreSQL team cases; 15 real Git/
private-protocol cases; eight workspace cases; three configuration cases; and 32
service-library cases, including atomic audit rollback and source-notice deduplication.
The previous physical source/receiver run remains separately evidenced at
`3564b027`; these later admission/configuration changes are not yet a complete
physical child-import qualification. The public HTTP/SDK isolated preset remains
gated while the durable child-import controller and submission/reception tools are
built. C4 is still in progress; C5–C7 remain pending.

Review item before C4 completion: when the repository itself is the workspace
root (`repository: "."`), reserved `.openworker` bookkeeping must not be staged
by an ordinary `git add -A`. Add an explicit bounded metadata exclusion/placement
rule and a real root-repository regression before qualifying that variant.

Latest source/configuration checkpoint validation: **32 service-library cases**
pass (2.08 seconds), including notice deduplication across repeated export/upload
reconciliation and later authority loss. The deliberately injected audit failure
requires reconnect-and-inspect, as intended; fixture setup reads durable metadata
as an observer rather than claiming the model worker's lease. All-target Clippy
passes for runtime, PostgreSQL, SDK (with test support) and service (OpenShell test
feature). No new production HTTP capability is enabled by this checkpoint.

Root-repository staging review resolved: fixed bounded local `info/exclude` rule
for first export/worktree dispatch, preserving user patterns and tracked ignore
files. Read-only recovery does not edit the exclusion. Real Git regression covers
`git add -A`, a preceding negative ignore pattern, unchanged tracked `.gitignore`,
and symlink refusal. Runtime validation: **16 handoff Git cases and eight workspace
cases pass**. Durable child-import controller is the next C4 slice.

### C4 child-import controller checkpoint — 2026-10-31

Implemented initial-source import outbox and bounded private controller. It pins
the original published source, child placement/profile and workspace request before
I/O, commits separate preparation/checkout dispatch bits, streams verified immutable
chunks, and atomically acknowledges source plus independent Git workspace before
admission. Unknown/revoked outcomes retain original work with deduplicated lead
notices. A source-selected reservation cannot fall through to generic preparation.
Submitted-revision lookup/tools and public isolated helpers remain gated.

Validation at this checkpoint: complete service library **35 cases pass** (2.62s),
including three new PG import cases; an additional pre-dispatch denial case is being
run. Linux runner **44 tests pass**, one child-fixture test intentionally ignored.
All-target strict Clippy passes (runtime/PostgreSQL/SDK/service plus OpenShell
feature). Preparing a physical controller qualification with two independent child
sandboxes. This entry does not claim that physical run has passed. Docker memory
is 16 GiB and host disk remains about 54 GiB free; no cloud resources provisioned.

Additional import validation: **37 complete service-library cases pass** (5.15s).
The new private-protocol fault injection loses a reply after chunk storage and
after checkout, then checks one preparation/write/checkout dispatch, recovery by
inspection, and admission only after the receipt commits. Pre-dispatch revocation
closes only the unstarted reservation. Strict all-target Clippy passes again.

The active OpenShell run has passed automatic imports into two separately
provisioned child sandboxes, independent Git metadata and subsequent child commits,
plus shared-worktree/preset and Rust/TypeScript regressions. Waiting for the complete
run and cleanup before recording it as a passing physical qualification.

Physical child-import qualification **passed in 362.54 seconds** at `1959dbe5`.
Two controllers automatically provisioned two independent child sandboxes, imported
the original pinned artifact, acknowledged independent Git receipts and only then
admitted the children. Both children made successful commits with their own Git
metadata. Existing shared-worktree, conflict, preset, worker-restart and Rust/TS
service checks also passed.

Evidence: [source/import controller run](evidence/coding-handoff-import-c4-20261031.json).
Test cleanup confirmed only the original OpenShell gateway remains and zero
temporary service/recording schemas remain. Host disk is about 54 GiB free. No cloud
resource or paid model was used. Five live qualification sandbox identities, runner
digest and host binary hash are recorded in the evidence.

C4 remains **in progress**: next build model-requested submission export/catalog,
submitted-revision verifier import, explicit lead reception/integration, then expose
and qualify the isolated Rust/TS preset. C5–C7 remain pending; do not merge this
partial C4 feature into the canonical branch yet.

### Illustrated scenarios and OpenShell Git acceptance — 2026-10-31

Added [workspace/source/GitHub diagrams](CODING-WORKSPACE-SCENARIOS.md), linked
from the SDK guide, design, handoff implementation and harness README. It separates
shared worktrees, independent offline bundle setup, original source acquisition,
submitted-code handoff, trusted GitHub publication and sandbox Git through OpenShell.
It labels actual C1–C4 qualification versus upcoming C5/C6 adapters. Corrected the
design's obsolete pre-C1 status summary. Configuration ownership includes the
distinction between OpenWorker RuntimeProfiles and OpenShell provider profiles and
credential-provider instances.

User emphasized that direct sandbox Git through OpenShell must be implemented and
tested for the OpenShell team. Make this an explicit C6 acceptance requirement:
real Git traffic through the OpenShell proxy, placeholder-only workload credentials,
clone/fetch allowed separately from push, destination/credential denial, credential
lifecycle and no raw-token fallback. Begin by checking the pinned local 0.1.3
gateway capability; current docs alone do not qualify our adapter. Local controlled
Git fixtures are authorized; a live GitHub write still needs a disposable approved
repository. This adds no implicit GitHub access to the default bundle path.

### C6 OpenShell Git early qualification — in progress

At the user's request, began the OpenShell Git capability/qualification slice while
C4 submission tools remain pending. Default sandbox profiles still have no attached
credential provider. Added explicit original provider/profile pins to the native
OpenShell adapter and opt-in Git launcher/credential-helper plumbing. Only
OpenShell-shaped references are forwarded; a raw token rejects. Helpers match exact
HTTPS authority and configured repository paths, never store credentials, and pass
current references to NEW process-manager commands rather than caching them in the
manager's environment. Offline handoff Git remains separate and credential-free.

Fast validation: credential scope/pinning cases pass on macOS; disposable Linux
execution passed 44 existing coding/handoff/workspace cases, three Git-plumbing
unit cases and two real helper/process transport cases. One helper-invocation test
initially failed because Git treated a shell-quoted executable as a named helper;
fixed with an explicit Git shell-helper invocation and reran successfully. Strict
runtime all-target Clippy passes.

A local Git HTTPS remote, private fixture CA and synthetic token are running in
namespace `ow-git-c6-20261031`, with two temporary `owgit-c6-*` OpenShell profiles/
providers. The agent image contains the PUBLIC fixture CA, not the server key/token.
Pinned gateway 0.1.3 requires omitting `tls` for automatic inspection; the literal
`terminate` was rejected by its profile importer, then corrected. Real proxy
qualification is running; do not count the adapter or C6 as qualified yet. No real
GitHub credential, GitHub write or cloud resource has been used.

Live qualification found two integration details rather than weakening checks:
provider/profile queries in the pinned gateway use a WorkspaceSelector message
(the local research checkout had the older string form); corrected the native wire
model against the exact upstream v0.1.3 proto and added a golden-wire test. Five
OpenShell adapter cases pass. The controlled Git TLS handshake then failed before
any upstream HTTP request. Logs confirm the network binary/destination were allowed.
OpenShell runs its proxy in a separate supervisor pod; the test CA must be trusted
there, not only in the agent image. Setting up a separate named fixture gateway
with a public-CA-only supervisor overlay. TLS verification remains enabled. The
failed diagnostic sandbox and temporary profiles/providers were removed from the
original gateway after UID checking; that gateway's configuration is unchanged.

The separate fixture gateway passed **real Git HTTPS clone/fetch** through
OpenShell's proxy using a synthetic upstream token and placeholder-only workload
credentials. The read-only profile blocked push (upstream receive-pack count zero);
the explicitly configured write profile allowed push. An unconfigured repository
rejected. Git configuration/remotes contained no token or saved helper. Both
agent and supervisor trusted the public fixture CA; TLS verification stayed on.

Expiry qualification exposed a real acknowledgment distinction: an immediate
`withheld/credential_expired` result can still contain the supervisor's OLD
observed provider revision. One repeat correctly failed when Git ran before the
supervisor updated. The fixture now observes that **original receipt**, requiring
the desired environment/config/attachment/hash acknowledgment before checking
denial. It never retries the mutation. Two deterministic Python contract cases
protect this condition, including replacement-receipt rejection.

The corrected repeat passed expiry denial, restored validity using a NEW tool
connection through the SAME sandbox-local process manager, provider detach,
existing-connection Git denial and rejection of new attachment. Cleanup then
deleted and confirmed the exact original sandbox even after credential detach;
UID, image, workspace and ownership remain mandatory for cleanup. The earlier
failed original was separately inspected and explicitly cleaned, without creating
a replacement for it. This tests validity/reference propagation, not an external
OAuth refresh flow or a live GitHub write.

Validation: **55 disposable-Linux tests pass** (44 existing coding/workspace/
handoff, 3 Git-scope, 6 provider/wire/cleanup and 2 real helper/process tests),
**37 complete service-library/PostgreSQL cases pass** (3.54s), two fixture control
contracts pass, and strict runtime/PostgreSQL/SDK/service all-target Clippy passes.
The complete physical C1–C4 service regression **passed in 369.94 seconds** on the
original gateway with a fresh digest-pinned runner. It reran Rust/TypeScript SDKs,
private types, two controllers, worker restart, shared worktrees, independent
placements, two automatic initial-source imports, exact Git receipts and preset
admission. Cleanup confirmed only the original gateway remains and zero temporary
service/recording database schemas remain.

The test provider instances/profiles and Helm release were removed after confirming
no retained sandbox. Its named namespace was deleted after UID/label checks, which
also removes the chart's retained fixture encryption-key Secret; its port-forward
was stopped. Other namespaces, the original gateway and shared test PostgreSQL
remain. Disk remains about 53 GiB free. No cloud resources, real GitHub keys or
paid model calls were used.

Evidence: [native Git/OpenShell qualification](evidence/coding-openshell-git-c6-20261031.json).
Repeat procedure: [fixture and lifecycle checks](../../harness/runtime/tests/fixtures/openshell-git/README.md).
The [scenario guide](CODING-WORKSPACE-SCENARIOS.md) now distinguishes this
qualified native path from the still-pending public RuntimeProfile/SDK grant
wiring. C4 submissions, C5 trusted publishing, remaining C6 wiring and C7 remain
in progress/pending; this is not a claim that the complete sequence is finished.

Strengthened the real Linux process test with TWO sequential MCP connections to
the same workspace/manager, each carrying a different synthetic reference. Both
the process environment and real `git credential fill` must use the second
connection's exact current reference, with neither reference in the result. This
catches stale manager caching even if an upstream temporarily accepts an older
credential. The full **55-case disposable Linux set passed again** after this
test change; no runtime behavior was changed by that test refinement.

### C6 service/SDK wiring — 2026-10-31

Continue the user-approved sequence with a named, operator-installed Git grant.
The service configuration owns the original provider/profile pin and exact Git
helper scope; a RuntimeProfile carries only its versioned grant name. Registration,
admission, worker launch and original-resource reconciliation select the same
immutable grant. Include the installed grant registry in installation identity;
changing it invalidates existing definitions rather than silently changing access.

Keep bundle/tool definitions unchanged. Trusted launch configuration adds Git
helper flags only to the installed coding runner, with settings bound to the
selected environment profile identity. This preserves team-private type identity
across placements and avoids accidentally inheriting a lead's launcher flags when
a child selects a credential-free profile.

Initial delegation allows the exact same named grant or no Git access. Different
credential-provider grants reject; no policy-subset inference from descriptive
labels. Existing file grants remain unsupported. New sandbox requests must match
the selected profile's exact network/credential grant sets and resource limits.
Rust and TypeScript expose the optional profile grant and a helper for deriving
those delegation sets. No raw secret or model-controlled OpenShell provider name.

Qualification will include fast registry/authority checks, real PostgreSQL ceiling
and admission cases, SDK serialization, and a local service + worker + OpenShell
Git run (including a credential-free child), followed by the existing regression.
C4/C5/C7 remain in the approved sequence after C6 wiring is verified.

C6 wiring checkpoint: named operator registry, optional RuntimeProfile `gitAccess`,
Rust `delegated_grants()` / TypeScript `runtimeProfileGrants()`, exact placement
sets and same-grant-or-none ceiling checks are implemented. Scope contributes to
environment identity; installed registry contributes to deployment identity.
Trusted runtime launch supplies Git flags without changing bundle/type identity.
Local container config and Helm schema now preserve non-secret registry entries
and reject inline credentials or a file-supplied selected-grant override.

Validation so far: 41 complete service/PostgreSQL cases pass (4.09s), 3 TypeScript
placement cases and 10 reference Helm cases pass, 4 local deployment-config cases
pass, and strict all-target runtime/PostgreSQL/SDK/service Clippy passes. The Linux
55-case fixture set passed after the first wiring changes. No cloud resources.

The first extended physical run passed the existing C1–C4 scenarios and actual
SDK-enabled Git clone/fetch plus read-only push denial, then failed because the
new test omitted a required team `policy` field. Cleanup completed. Corrected the
test, retaining the default full suite and adding an explicit `OW_GIT_ONLY=true`
focused rerun mode that requires the local Git fixture. Do not count that failed
run as a full C6 pass. A new physical run is active.

Additional review found that non-secret OpenShell provider configuration can
parameterize policy. The provider pin now includes an optional configuration
hash (None requires empty configuration); credential maps remain undecoded and
rotation is not part of the policy pin. Confirmed the map's wire field against
upstream pinned v0.1.3 `datamodel.proto`, added a real protobuf decode/digest case,
and all 7 native provider tests pass. This closes a config-change gap rather than
silently accepting an altered original provider.

The focused SDK/OpenShell run passed in 198.16s after moving team creation before
its lead's first turn (the second failed attempt had violated that existing team
lifecycle rule). Added a real PostgreSQL/API preparation case, which passes. A
stronger focused run then passed in **228.39s**: read-only clone/fetch and push
denial, explicit write-profile publication to the synthetic remote, forbidden
cross-grant delegation with inspection before corrected setup, same-grant child,
credential-free private child, worker restart, and TypeScript profile registration/
resume/fetch. All owned sandboxes were deleted by the test. The remote recorded
one successful receive-pack and only the new `agents/sdk-*` branch; base unchanged.

Additional validation: 42 service/PostgreSQL cases pass (3.44s), 10 TypeScript
service transport cases, 3 placement helpers, 10 Helm rendering/security cases and
4 local configuration cases pass. Host runtime library passed all 80 cases before
the lock guard refinement and all **81** afterward. All-target strict Clippy passes.
Loopback TypeScript fixtures require the sandbox's normal network permission;
they passed when rerun with it rather than changing test behavior.

The final Linux run initially exposed an existing editing-lock race: closing the
parent FD can leave its flock held by an unrelated concurrently forked child until
exec/exit. Added an explicit unlock-on-drop guard and deterministic duplicated-FD
regression (also checks the next owner remains protected). No timing sleeps or
serial-test workaround. The complete Linux selection now passes **57 cases**,
including the new lock test and provider configuration pin test (70.6s build/test
step). Rebuilt the Git-enabled runner overlay from the descriptor-verified local
base; no remote private-image pull or credential was required.

Running the default **combined** C1–C4 plus C6 suite with read/write profiles and
that latest digest-pinned Linux runner before closing the C6 checkpoint. The
focused flag is unset in this run; do not confuse the focused pass with a combined
pass. No cloud resources or paid models were used; host disk remains ~52 GiB free.
A pre-existing unit-test schema dated 00:16 UTC was identified and retained; fresh
service/recording schemas and test sandboxes were cleaned between attempts.


C6 final qualification: the default combined C1–C4/C6 service suite passed in
**551.66s** with the latest digest-pinned Linux ToolRunner. A further **296.55s**
focused run passed after adding shared-member credential capability checks. It
also covered independent same-grant and credential-free children, actual read-only
Git denial, explicit write-profile publication, rejected cross-grant delegation,
worker restart and Rust/TypeScript resume. A shared sandbox shares its credential
capability; agent cwd isolation does not make that capability private.

The synthetic remote recorded three authorized publications to distinct
`agents/sdk-*` branches across successful read/write runs. Its original master
commit was unchanged. No real GitHub token, paid model or cloud resource was used.
The final Helm schema rerun passed all 10 cases. Sanitized evidence:
[C6 service/SDK qualification](evidence/coding-openshell-git-service-c6-20261031.json).

All test sandboxes were removed. Both synthetic provider/profile pairs, the
isolated Helm release, namespace and own port-forward were removed. The existing
shared gateway remains running. Historical pre-existing PostgreSQL unit schema
was retained; this qualification's service/recording schemas were cleaned.
C6 is verified on the feature branch; continue C4 submission workflow, C5 trusted
publication and C7 combined qualification before merging the stacked feature.

### C4 submitted-revision workflow — 2026-10-31

C6 checkpoint `5f3dc53f` is pushed. Continuing C4 on the same stacked branch.
Model submission, bounded catalog reads and attributed verification reports now
have portable domain operations. Submission identity binds producer/source/commit
and deduplicates across different model call IDs; changing summary text cannot
create another export after an uncertain response. Reports are separate from the
frozen manifest and require the caller's acknowledged exact-submission import.

Implementing a separate PostgreSQL submission outbox, private artifact origin,
controller export/upload and selected-submission import. The controller follows
lead → team → outbox lock order and never locks a producer after taking the team
lock. Private artifact registration locks its producer before lead/team. Artifact
store credentials never enter the sandbox. Pending/unknown handoffs retain their
original environment after member retirement. New contract and real PostgreSQL
cases are being run; this is not yet a C4 completion claim.

C4 continued: the expanded real-PostgreSQL suite passes **45 cases (3.71s)**,
including immutable submission publication, exact verifier selection, independent
lead reception, integration dispatch/recovery fencing, revocation and retaining
pending work after ordinary session closure. The disposable Linux selection passes
**62 cases (58.5s build/test step)**, including SHA-1/SHA-256 integration, stale/
dirty/divergent rejection and inspection of a lost receipt without another mutation.

Two physical attempts exposed SDK wiring omissions before the full new workflow
could pass: delegated sessions lacked their original release/profile identity in
snapshots, and the shared Rust ChildOptions type lacked the source selection
field. Added receipt-derived child service identity without relaxing SDK identity
checks, and source selection to Rust/TypeScript child options. The second run was
compiled before that source-field correction and is allowed to finish its own
cleanup. These failed attempts do not count as C4 qualification.

Public team creation now accepts application-owned coding defaults and obtains
artifact binding from server configuration, never request JSON. Added isolated
service preset factories and role prompts in the shared Rust/TypeScript preset
catalog. Mutating handoff tools use the existing Ask/AutoApprove/Bypass gate;
reception never switches the lead's source workspace. Integration uses an explicit
expected-head CAS, with terminal known rejections and inspection-only unknown
partial outcomes. Git index/files and branch-ref writes are separate operations;
this is not a cross-filesystem transaction and recovery never repeats read-tree.

Next physical run must use the new source-selection SDK field and latest server;
current Linux integration runner is pinned at
`sha256:37bdb22f469c756aeb67dd488fa2b8c2a4f9f3dde4f04d9b2b88a974339e40ab`.

The corrected third physical attempt reached actual submission export/publication
and verifier import, then failed its real-output assertion: the verifier's normal
long checkout path made the retained process manager's filesystem Unix socket
108 bytes, beyond Linux's pathname capacity. Its scripted report still claimed
verification; the test correctly rejected that claim using actual tool output.
No source integration was attempted in that failed run; owned sandbox cleanup ran.

Fix the manager endpoint rather than shortening instance names. Existing short
filesystem endpoints retain compatibility. For long Linux paths, use a bounded
abstract Unix address derived from the original private directory path, device,
inode and effective UID; retain the ownership lock and peer-UID checks. Add both
address/replacement-identity tests and a real long-workspace reconnect/stop case in
the disposable Linux fixture. Also exclude private process-state bookkeeping from
root-repository staging. Rebuild/qualify the runner before another physical attempt.
The prior 62-case Linux pass predates this manager fix. One rerun invocation used
an incorrect Docker build context; no image was produced. Use an absolute context
path for the corrected build. No runtime safety behavior was relaxed.

Build-environment correction: the context was filtered by the repository's
Python-image `.dockerignore`. The new private Dockerfile also needs the existing
Rust qualification Dockerfile-specific ignore list. Copied that curated list to
the new private build definition; continue excluding target directories, Node
modules, environment files and databases. The two filtered-context attempts
produced no image. Also corrected excess quoting in the new focused fixture's
shell strings before running that fixture.


C4 focused qualification passed in **264.14s** using runner
`sha256:fe3cecc90c5d984fbc38d1dc13e879f131fa5734604aec3f77fc9f661829d287`.
Actual source output proves builder export/publication, exact-revision independent
Rust and TypeScript verifiers, attributed reports, lead reception, terminal stale
HEAD rejection, expected-head integration and source read-back. Owned sandboxes
and database schemas were cleaned. This focused pass is not the default combined
regression, which is running separately.

The rebuilt disposable Linux selection passes **68 cases**, including the long
workspace process-manager endpoint/reconnect/stop regression. All **11 TypeScript
service transport cases** pass. Existing four-mode Rust preset regressions are
running against SQLite/PostgreSQL and embedded/managed hosts. Retirement/cancel
races and combined mixed-team recovery remain part of C7; no claim that C7 or C5
has been completed. Disk remains approximately 50 GiB free; no cloud resources or
paid models were used.


The first default combined C4 run failed after **557.38s**: the earlier C1
scenario intentionally advanced the shared lead repository to its UI branch,
but the later C4 integration still supplied the original base as expected HEAD.
The runtime correctly persisted a stale-head rejection. The fixture then waited
for an impossible success and its separate read-back failed without asserting
stdout. No branch protection was relaxed. Give C4 its own independent source
checkout at the original base, assert actual pre-integration output, and fail
immediately if the intended expected-head operation is rejected. All owned
sandboxes/schemas were removed after the failed run. The focused C4 pass remains
valid, but does not substitute for rerunning this corrected combined test.

Final fast rerun: **83 runtime library + 7 portable + 20 real Git handoff cases**
pass; all **45 service/PostgreSQL cases** pass (5.32s). Four-mode Rust preset
regressions pass (6 tests, 81.80s), with only the two newly identified disposable
PostgreSQL schemas removed afterward. Historical fixture schemas were retained.
Strict all-target Clippy passes (32.70s). The SDK now reexports source-choice and
Git object-format types so applications need no direct runtime import for them.


### C5 implementation continuation — 2026-10-31

C4 source checkpoint: `9ed64532`; corrected default OpenShell rerun is active.
Continue on `feat/coding-trusted-git-c5-20261031`, retaining the approved sequence.
[Trusted Git broker design/build order](TRUSTED-GIT-BROKER.md) separates optional
host-side private staging/publication from C6 sandbox CLI credentials. Start with
real controlled-remote adapter contracts, then wire RuntimeProfiles, durable
service operations and remote-source admission. No live GitHub write is implied.
Do not overwrite the server executable while the active C4 test owns it, and do
not merge unverified broker code with the verified C4 checkpoint.


The second combined attempt failed after **586.81s**. The new clone was left on a
detached HEAD; native integration requires an ordinary branch and correctly did
not dispatch it. Changed the setup checkout to an explicitly named `handoff-main`
branch. During the wait, PostgreSQL also terminated one idle-in-transaction
connection (3.75s configured timeout) and the client returned an explicit 503
unknown outcome. No mutation replay or timeout relaxation was added. Rerun the
corrected full fixture without concurrent build/Git test load; retain this timeout
observation for C7 recovery qualification. Its owned resources were cleaned.

C5 adapter checkpoint (not service completion): a separate optional
`openworker-git-broker` crate passes **9 controlled-remote tests (3.75s)** covering
SHA-1/SHA-256 source staging and publication, no project-file checkout in the
trusted process, create-only conflict, expected-tip fast-forward, divergence,
lost receipt/inspection without replay, changed remote head remaining unknown,
scope denial, expiry/revocation, corrupt bytes, operation changes, symlink/lease
denial and redacted command diagnostics. These are filesystem Git fixtures, not
an authenticated HTTPS, GitHub or complete service qualification. Service/SDK,
private-source admission and combined C7 wiring remain to be built/verified.


Local environment health checkpoint (2026-11-01 UTC; continuing October 31 work):
a subsequent full rerun failed after **77.60s**, before C4, with two PostgreSQL
unknown-outcome errors and an interrupted turn. The Docker-hosted database logged
idle-in-transaction termination under heavy host/VM load. A separate disposable
native PostgreSQL 14.19 loopback trial retained the same timeouts, but failed
before sandbox creation with an OpenShell TLS handshake EOF (**52.37s**). The
shared gateway had 17 restarts and liveness/readiness timeouts; observed host load
exceeded 200 despite ample Docker disk space. No timeout or safety gate was
relaxed. These failed runs do not qualify C4 or PostgreSQL 14 for deployment.

Inspected the native trial's exact deterministic sandbox name through the original
OpenShell endpoint: not found. Removed only that trial's service/recording schemas
and stopped its dedicated native PostgreSQL. Existing Docker PostgreSQL, gateway,
demo namespaces and Langfuse remain unchanged. An optional user question asks to
pause/restore older demo stacks for qualification; no answer means no permission
to pause them. Continue implementation and fast tests while this is unresolved.
The full test's early setup cleanup needs strengthening in C7: its existing caught
panic cleanup begins after initial provider setup, so this early failure needed
explicit original-resource reconciliation. C4 has a passing focused physical run,
but its corrected default combined run remains unverified; do not merge it yet.


C6 public checkpoint: fast-forwarded the approved canonical public branch from
`f8b31eea` to **`5f3dc53f`**, excluding the unverified final C4 and C5 changes.
The fully verified initial-source primitives and C6 named RuntimeProfile/SDK
OpenShell Git access are now available to partners. The C4 source checkpoint
`9ed64532` and subsequent work remain on the feature stack until qualification.

C5 initial registry/Profile wiring compiles with the optional `trusted-git` service
feature (35.90s). Portable contracts remain in the runtime; Git execution is in an
optional separate crate. Add the publication outbox/controller on the same policy,
approval and event framework, and recheck authority immediately before a push.
Controller failover must inspect its original remote ref using the durable DB
intent even when the new node lacks the old Git cache. Initial remote-source
staging needs its original trusted cache until artifact publication; cache loss
must remain unknown rather than generating different bytes under the same ID.


C5 correction before release: publication must be a normal registered native tool,
`github_broker.publish_code`, selected by each bundle's fully qualified tool list.
A RuntimeProfile grant is the independent scope ceiling, not automatic visibility
for every coding member. Removed the draft intrinsic publication tool/schema.
The native callback queues only its exact currently executing worker-owned call;
ordinary REST/team commands cannot forge a native ToolContext. The broker stays
outside agent sandboxes. This follows the earlier partner ToolRegistry agreement.

Wired the reference service's existing v3 registry through installed/release/scoped
type bundle loading, preserving v3 selection rather than serializing resolved
native/HTTP tools as legacy v2. Registry descriptors contribute to installation
identity. Fixed native FQN selection (avoid `namespace.namespace.tool`) while
preserving legacy v2 behavior. Initial registry build check passed (30.48s); the
new callback/storage tests are still being updated and are not yet qualified.


### Recovery after laptop restart — November 1

Verified that source and working-tree changes survived. Public/local tracked
`openworker-harness-sdk` points to verified C6 commit `5f3dc53f`; the active
`feat/coding-trusted-git-c5-20261031` branch is at C4 fixture correction `702c46a2`
(parent C4 implementation `9ed64532`). C5 code remains uncommitted in this worktree.
Approximately 55 GiB free. Docker is not running and the local Kubernetes endpoint
is unavailable. Temporary `/private/tmp` test logs/fixtures checked after reboot
are gone; recreate them before further physical qualification. No tests were
rerun in this recovery check.

Last recorded C5 results: registered v3/native/MCP selection regressions passed
(two tests); strict native-tool/service all-target Clippy passed (23.48s). Three
new PostgreSQL publication cases reached a real integration gap: initial remote
profiles currently reject all servers other than `$OPENWORKER`, including explicit
registered native tools. Support for trusted host-native callbacks beside sandbox
MCPs needs an explicit placement implementation and tests; do not merely remove
the guard or claim C5 complete. The native PostgreSQL fixture was stopped after
the failed component run. The failed isolated gateway Helm release/namespace was
removed and namespace absence confirmed. Existing demos were not paused.

Resume order: restore local infrastructure/owned fixtures; verify corrected C4
candidate in its isolated worktree and merge once qualified; finish C5 registered
native placement, publication and remote-source admission; then C7 combined
parallel/mixed-placement and recovery qualification. Earlier test counts in this
journal are historical evidence, not post-restart rerun claims.


### C4 — complete post-restart qualification, November 1

Clean candidate `702c46a2` (implementation `9ed64532`) passed the entire default
`service_v2` local PostgreSQL 17 + Kubernetes/OpenShell qualification in **550.56s**.
This includes two controllers, worker restart, shared worktrees, independently
provisioned source checkouts, artifact publication, builder submission deduplication,
exact-revision Rust and TypeScript verifiers, independent lead reception, stale-head
rejection and explicit expected-head integration with actual source-file read-back.
The fixture deleted/confirmed its original sandbox identities and dropped both
owned schemas. Existing demo namespaces and Langfuse were left untouched.

[Sanitized C4 evidence](evidence/coding-artifact-handoff-c4-20261101.json) records
source/image/binary/log hashes. This uses deterministic model calls and real tool
execution; live-model combined qualification belongs to C7. C5 remains separate
unverified work. No cloud resources or public-main changes were made.

C4 public merge: `b829af5b` is now the canonical public feature branch. C5 remains
on its feature worktree. Restored Docker/Kubernetes/OpenShell after reboot, with
loopback mTLS forwarding, a PostgreSQL 17 container limited to 512 MB/1 CPU and
persistent named fixture storage. About 55 GiB free. No old demos were paused.

C5 placement fixes: explicitly selected native callbacks run on the trusted host,
image-bundled coding MCPs run in the sandbox, and local resource grants/external
MCP executables remain rejected for this remote profile. Persisted registry and
placement identity is checked on reload/child placement; native callbacks do not
claim process or kernel isolation. Real PG publication tests passed **3/3 (9.49s)**;
all ignored service-team-type regressions with the feature passed **34/34 (14.47s)**.
Controlled real Git tests passed **11/11 (4.45s)**, including failover without the
old publication cache and a final authorization check immediately before push.
Corrected an outdated test that expected missing local cache to forbid an explicit
inspection-only remote read; it still proves no new push marker is written.

Remote-source wiring is in progress: app-owned `remote_ref` + exact commit,
profile-owned trusted grant, immutable artifact staging and a durable lead checkout
before accepting the lead's first turn. Preparation/checkout dispatch bits survive
replacement; lost checkout replies inspect only the original receipt. This new
path is not qualified yet. Public SDK snapshots expose bounded source setup status.
The test transfer engine is reused rather than introducing another artifact path.

C5 HTTPS qualification passed **1/1 (2.44s)** with synthetic credentials, real Git
HTTP transport and verified TLS. It proves pinned fetch, branch publication, bad-key
rejection/redacted diagnostics and inspection without a second receive-pack. This
is a loopback TLS fixture, not a real GitHub write. Production origin remains fixed
GitHub HTTPS; arbitrary endpoint/CA overrides compile only for disposable fixtures.

The first physical C5 attempt correctly rejected a fixture alias collision. The
next imported source and admitted a real independent builder, but its legacy tool
alias was not selected in the v3 bundle. Corrected the fixture aliases, then found
an automatic scripted inbox wake falling through to unrelated ExampleApp tools;
fixed that scripted-only behavior. Bounded the workflow deadline. Corrected fixture
cleanup to reconstruct each original RuntimeProfile provider, not the deployment's
unscoped provider, and reconciled only those failed fixtures' exact sandbox IDs.
These failed runs are not C5 qualification evidence.

C7 edge-case work: finish-current retirement now pins the originating turn so an
already accepted asynchronous handoff can settle after session closure; it cannot
admit a later turn or cancelled effect. A new real-PG regression distinguishes it
from cancel retirement. Expanded PG suite passed **36/36 (16.60s)** including source
setup's one-time prepare/checkout, stale receipt rejection and first-turn gate.
TypeScript build passed; its loopback HTTP/preset/profile tests passed **18/18**
after running with the required local listener permission. Further C5 physical
qualification, mixed-team parallelism and bounded live-model work remain pending.

OpenShell capacity finding: the original shared 0.1.3 gateway hit its per-caller
**1,000 durable mutation admissions** within the **24-hour successful-receipt
retention** window. The upstream implementation counts retained successes as well
as unresolved claims. No receipt ledger was cleared, safety gate weakened or
uncertain tool action replayed. The original failed C5 fixture's remaining original
sandbox IDs/DB receipts are retained for reconciliation once that gateway accepts
cleanup. These do not qualify as cleaned resources. C4 had finished its cleanup
before the cap was reached.

Installed a separate bounded local qualification gateway in
`ow-coding-shell-20261101` from the pinned OpenShell v0.1.3 source/chart with the
same existing non-secret resource/security values and independent persistent
state/mTLS. Existing `ow-helm-shell`, demos and Langfuse are unchanged. This avoids
claiming that an old full admission ledger is a harness Git failure. Repeated
large-scale production workloads must account for this upstream limit; record it
in partner guidance and requalify any future upstream reconciliation change.

C5 found a real end-to-end integration issue after successful source staging,
builder submission, independent verifier and lead reception: a remotely imported
lead source was detached, while explicit integration requires an ordinary local
branch for expected-head CAS. Changed fresh independent imports to the deterministic
local `openworker/import` branch, preserving the exact pinned commit, own `.git`,
transfer identity and no-replay checkout rule. Added a both-object-format regression
and am rebuilding the Linux ToolRunner; the prior physical run timed out safely on
an unknown integration outcome and cleaned its isolated fixture. It is not a pass.

Strengthened registered publication's transaction boundary to recheck the exact
native-tool approval (bundle/schema/resource identity, FQN and live session grant)
immediately before committing an intent. A new PG race test checks missing approval,
revoked Allow Always and valid Allow Once. The first test attempt correctly refused
a stale fixture session revision; the fixture now reloads revisions between edits.
Publication authority and uncertainty notices use the existing durable inbox.


### C5 — complete trusted Git workflow passed, November 1

The corrected physical test passed **163.53s** against isolated OpenShell 0.1.3,
PostgreSQL 17 and the rebuilt digest-pinned Linux runner. It fetched exact private
source through the trusted broker, acknowledged the lead checkout before first
turn, admitted independent builder/verifier sandboxes, verified exact submitted
Git/artifact provenance, received and integrated source using expected-head CAS,
and executed the selected registered native publisher. Main stayed unchanged;
the allowed agents/ branch contained the exact submitted commit. A second call
ID reused the original publication. The test deleted/confirmed its original
sandbox identities and dropped its schema.

[Sanitized C5 evidence](evidence/coding-trusted-git-c5-20261101.json) records image,
runner/server and log identities. Host unit **84**, Linux unit **91**, Git **20**
on each platform, source contract **3**, registry selection **2**, PG **37**, broker
Git **11**, authenticated TLS **1**, and TypeScript wire/preset/profile **18** cases
passed. All six Rust coding preset cases passed: two contract cases, SQLite
embedded/managed (**64.02s**) and PostgreSQL embedded/managed (**93.67s**).
Strict all-target broker/service Clippy passed after a test-only formatting fix.

The ordinary runtime has no Git execution dependency; trusted service packaging
is an opt-in target and is being built/smoke-checked. Source setup and controller
events, approval/grant fences, uncertainty inbox notices and ordinary tool
classification remain independent checks. C7 parallel and bounded live-model
qualification follows; do not count its definitions as completed evidence.

C5 canonical public checkpoint pushed: **`5d3534a4`**. C7's real mixed-placement test
passed **167.33s**: two teams, four builders, four member-sandbox verifiers, shared
and independent sandboxes, worker replacement before execution, and retirement
admission. Database audit intervals prove all four initial tool calls overlapped;
concurrent API submissions alone were not used as proof. Both five-instance rosters
and exact per-builder verifier reads passed; owned sandboxes/schema were removed.

The production `trusted-git` feature also passed strict library Clippy with the
fixture feature disabled (**2.50s**). Optional service-image build continues.
A bounded live-model test now uses an available `claude-sonnet-4-6` ID confirmed
through the provider model-list endpoint and the previously authorized key file.
The key is passed only to the already-built test/runtime processes, never compiler
scripts, source configuration, ToolRunner or logs. It is not yet a passing result.

C7 live-model qualification passed **195.16s** with `claude-sonnet-4-6`: the lead
spawned an independent builder and an exact-submission verifier. Real generated
counter HTML, artifact checksum, self-contained Git integrity and recorded verifier
HTML reads were checked independently of the agents' claims. No branch publication,
integration, PR, cloud provisioning or credential exposure was allowed in this case.
The successful fixture deleted its original sandbox identities and dropped its schema.

The opt-in trusted-Git Linux service image built successfully (release build **226.7s**)
and passed a non-root UID 1000, read-only, network-disabled smoke: server CLI loads,
Git is installed and Python is absent. The image is `openworker-service:trusted-git-c5-20261101`
with digest `sha256:ff598854018fdd0de4377cd722d198bcbdfe9c2b0320ff7a88806bc636960a44`.
The default/core packaging remains unchanged. Final combined legacy/v3 regression
is running on the rebuilt runner before C7 is marked complete/merged.


### C7 — local sequence complete, November 1

Final full service regression passed **308.46s** with the rebuilt Linux runner:
legacy and registered bundles, immutable releases, two controllers, worker restart,
shared worktrees, independent sources, exact artifact submissions, Rust/TypeScript
verifiers, stale-head rejection, explicit integration and original owned cleanup.
The isolated gateway contains only its ready gateway pod after successful cases.
No successful-case service/recording schema remains.

The six Rust preset cases passed on both storage/process choices. Found that the
older SDK PG fixture kept its generated schemas; added caught-failure cleanup and
a test-only PostgreSQL dependency, then reran both PG modes (**69.94s**, pass) and
verified those schemas were removed. Removed only the two earlier known generated
SDK schemas. This adds no production SDK dependency. Final strict all-target
Clippy for runtime-dependent broker/service/SDK passed (**10.66s**); formatting,
diff and acceptance-driver shell syntax checks passed.

[Sanitized C7 evidence](evidence/coding-parallel-live-c7-20261101.json) links source,
images, actual physical/live results and log hashes. The grouped
[test catalog](CODING-AGENT-PRESETS-TESTING.md) explains what/why for each layer.
The reusable `harness/tests/coding-qualification.sh` driver separates fast, storage,
cluster, authenticated HTTPS and explicitly enabled live-model checks. The Linux
coding-tests image target includes the new Git/contract/registry cases. Current
guides now link the available trusted source/publication path instead of calling
C4–C6 upcoming.

Retained exception: three original sandbox identities and the original failed C5
schema remain on `ow-helm-shell`, whose caller hit the upstream durable admission
quota. The evidence records exact identities. They are not represented as deleted;
no gateway ledger, user work or unrelated demo was cleared. Reconcile these exact
objects after that gateway's admission capacity recovers. The fresh qualification
gateway/PG fixture are bounded and retained for local follow-up; about **52 GiB**
free. Cloud trials, actual GitHub writes and Windows trusted-Git execution remain
outside these local qualification claims. No public-main merge or PR was created.
