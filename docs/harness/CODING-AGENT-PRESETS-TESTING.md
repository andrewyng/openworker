# Coding preset qualification

This catalog explains what each test layer proves. The
[progress journal](CODING-AGENT-PRESETS-PLAN.md) records completed runs, source
commits, failures, cleanup and remaining milestones. A listed case is not evidence
of a successful run by itself.

## Workspace identity and ordinary file access

Fast runtime tests use real temporary Git repositories and linked worktrees.
They check distinct child directories, traversal/symlink rejection, original
directory identity, branch identity and the private/common Git metadata pointers.
An acknowledged child may advance its commit; replacing its directory or borrowing
another child's `.git` pointer rejects. Unregistered external metadata is refused
before repository configuration is inspected.

These tests establish the intended file-tool scope and narrow Git exception.
They do **not** turn cwd into a process sandbox: shell commands in a shared
sandbox retain that sandbox's aggregate access.

Cases: [workspace receipt tests](../../harness/runtime/tests/team_workspace.rs),
[coding file/process tests](../../harness/runtime/tests/coding.rs),
[Git/search/patch regressions](../../harness/runtime/tests/coding_extended.rs).
Linux additionally checks the actual shell cwd and process lifecycle; the macOS
suite checks that unsupported process modes fail explicitly.

## Durable controller intent and admission

Real PostgreSQL tests simulate a controller disappearing after dispatch was
committed. A competing controller cannot dispatch during its lease. After lease
expiry, the new owner receives the original operation with inspect-only authority.
Stale owners and changed receipts reject. Child admission binds the saved receipt,
cwd and original environment; ordinary clients cannot call controller methods.
Unknown preparation closes only the unstarted child and does not block a normal
same-directory reservation.

These cases test transactional ownership and restoration without requiring an
OpenShell gateway. Filesystem identities in this layer are synthetic; the next
layer checks them against real files.

Cases: `shared_workspace_intent_is_leased_once_and_admission_pins_receipt` and
`unknown_workspace_outcome_closes_only_unstarted_child` in
[service tests](../../harness/server-reference/src/service_team_types_tests.rs).
Existing tests also cover activated release/profile constraints, private type
approval, original model references, API authorization and legacy empty receipts.

## SDK and storage parity

The same directory/worktree scenario runs in Rust embedded and managed subprocess
modes with SQLite and PostgreSQL. It checks stable reservation IDs, actual scoped
file reads, failed preparation, restart restoration and public snapshot redaction.
TypeScript HTTP/SSE tests check service identity, cursor replay, credential refresh
and preservation of idempotency keys after uncertain transport outcomes.

This protects the choice of storage or process placement from becoming a feature
switch. It does not assert native process support on unqualified platforms.

Cases: [four-mode workspace suite](../../harness/harness-sdk/tests/team_workspaces.rs)
and [TypeScript service tests](../../harness/typescript-sdk/test/service.test.mjs).

## Real local Kubernetes and OpenShell

The service qualification uses two API processes, competing controllers, the real
catalog worker and a digest-pinned Linux ToolRunner in local OpenShell sandboxes.
It recovers a provider create whose acknowledgement was lost, runs private child
types before/after worker replacement, and starts two builders in distinct Git
worktrees of the original sandbox. Builders write/commit independently; recorded
exit codes and output prove their cwd and Git operations. A sibling-path write is
denied before dispatch. A verifier checks the submitted commit, and the lead
explicitly inspects both builder commits. TypeScript reconnects to this service
and checks a shared verifier plus a separate environment.

The fixture deletes its original provider resources and drops only its own
execution/recording schemas, including after caught test failures. No API keys,
paid model calls, cloud resources or GitHub writes are needed for this layer.

Case: [service/OpenShell qualification](../../harness/server-reference/tests/service_v2.rs),
with [TypeScript live service checks](../../harness/typescript-sdk/test/service-v2-live.mjs).

## Versioned presets and default worktrees

The Rust preset suite creates a single-agent HTML output, then starts a shared
team without automatically creating children. Two builder instances receive
distinct worktrees from one pinned base; writes remain separate, the source
checkout stays unchanged, incompatible team reconfiguration rejects, and restart
preserves the roster. Run it with SQLite/PostgreSQL and embedded/managed modes.
The TypeScript suite exercises dynamic preset registration, real streamed tool
events, default worktree resolution and restart over private stdio on both stores.

The shared transaction contract checks distinct deterministic paths/branches,
pinned base, retry identities, explicit verifier directories and rejection of
changed defaults on both databases. Service qualification deploys actual
preset-derived releases, checks RuntimeProfile compatibility and runs a default
builder worktree through OpenShell. Factory tests prevent implicit bypass/model
selection, mutable shared configuration and silently dropped service policies.

Cases: [Rust preset qualification](../../harness/harness-sdk/tests/coding_presets.rs),
[TypeScript preset qualification](../../harness/typescript-sdk/test/coding-presets-live.test.mjs),
[shared database contract](../../harness/tests/agent_team_contract.rs), and the
service/OpenShell suite above. The TypeScript configuration copy is checked against
the Rust-owned versioned JSON; no agent loop is duplicated across SDKs.

## Later milestones

Delegated isolated placement, artifact handoff, constrained
GitHub publishing, CLI credentials and the combined multi-team recovery matrix
are tracked separately as C3–C7. Passing C1/C2 does not qualify those features.

## Child environment allocation and recovery

The PostgreSQL placement outbox contracts check original per-instance identity,
atomic ownership/quota accounting, one create dispatch, inspect-only recovery,
claim expiration and stale result rejection. Concurrent reservations cannot
exceed the submitting principal's quota. Unknown create outcomes retain quota.
Pause blocks first dispatch; revocation or retirement cancels only an allocation
proven never dispatched. Inspection of an already dispatched original resource
remains possible after revocation, without authorizing a child tool/model turn.
Changed profiles and foreign deployment identities reject before allocation.

Cases: five `placement_outbox_*` tests in
[provider outbox contracts](../../harness/server-reference/src/service_team_placements_tests.rs).
These use isolated PostgreSQL schemas and synthetic provider observations, with
no gateway/model traffic. They establish database/controller contracts, not
physical sandbox isolation or resource enforcement. These contracts alone do not enable a provider; the later physical OpenShell
section and journal record the service adapter's qualification.

## Original child bindings, retention and audit lock ordering

Additional real PostgreSQL cases use ordinary service configuration to bind a
New child, reuse that sandbox for another member, and refuse a forged switch to
the lead's otherwise compatible environment. Closing/retiring the first member
cannot remove another reserved or active member's deletion reference. A controller
can finish retirement after session closure committed before a crash. Revocation
after binding closes only the unstarted child and keeps owned work available for
explicit original-resource cleanup; the existing deletion queue selects the
original deployment/profile.

A deterministic lock test holds the lead row, blocks an authenticated submission
on it, and proves trusted child admission can still acquire the audit append
lock. This prevents the observed submission-versus-child-creation deadlock without
automatically replaying an uncertain action. A separate case proves database
errors keep their sanitized infrastructure type rather than turning into a
permanent placement denial. Existing artifact/control/submission regressions
check that moving audit appends preserves transactional rollback and attribution.

Cases: `placement_binding_member_reuse_retirement_and_original_cleanup`,
`placement_revoked_after_binding_never_admits_child_or_destroys_retained_work`,
`placement_admission_cannot_deadlock_turn_submission_on_security_audit` and
`placement_controller_distinguishes_sanitized_storage_outage_from_denial` in
[placement contracts](../../harness/server-reference/src/service_team_placements_tests.rs).

The expanded [OpenShell suite](../../harness/server-reference/tests/service_v2.rs)
also creates two instances of the same builder in separate smaller sandboxes,
loses a real child-create reply, restarts competing controllers and checks the
original UID. A private verifier reads the builder's selected existing directory;
another child and the lead prove those files are absent from their environments.
Profile identity and physical Kubernetes limits are checked. The same run retains
the shared worktree/commit/conflict workflow and exercises TypeScript service
placement configuration and member execution. Cleanup uses each original target
profile, including unacknowledged creations, and confirms deletion.

## Handoff metadata and real Git bundle integrity

The C4 portable contract checks exact repository/base/submitted identity, producer
and artifact binding, SHA-1/SHA-256 formats, size/digest bounds and portable import
paths. Reports cannot refer to another submitted revision or declare an approval.
These are data checks, not evidence of artifact ownership or publication.

Real temporary Git repositories export one exact ref and transfer binary, renamed,
deleted and new files into independent Git metadata. Re-entry preserves the
original result even after HEAD advances. Missing/incomplete bundles never trigger
another export. Tests reject changed refs or repository identity, corrupt bytes,
marker symlinks, unsafe helpers, external object stores, replacements, missing
prerequisites, extra advertised refs and hidden unreachable objects. A real
incompressible 65 MiB source verifies that the 64 MiB stream cap stops without
publishing a receipt or automatically restarting on inspection.

Cases: [portable manifest/import tests](../../harness/runtime/tests/coding_handoff.rs)
and [real Git bundle cases](../../harness/runtime/tests/coding_handoff_git.rs).
These currently qualify local Unix mechanics. They do not yet qualify artifact
transfer, model-facing tools, isolated preset/defaults or provider staging; those
C4 layers remain explicitly tracked in the journal.

## Artifact setup provenance

Artifact-store contracts check that ordinary turn receipts retain their existing
serialized identity, while application setup uses an explicit team/setup origin
with no fabricated LLM turn. PostgreSQL tests prove that a setup label alone
cannot register an artifact, and the public API request schema rejects attempts
to select that trusted origin. This prevents model/client metadata from being
mistaken for source-setup authority. The trusted source-intent controller remains
a separate qualification requirement.

Cases: [artifact-store contracts](../../harness/artifacts/tests/store.rs),
[PostgreSQL artifact contracts](../../harness/store-postgres/tests/artifacts.rs),
and the `origin_tests` module in
[artifact API](../../harness/server-reference/src/artifacts.rs).

## Bounded private transfer and independent checkout

Real Git repositories and separate ToolRunner subprocesses check bounded chunks,
immutable same-byte retransmission, missing chunks, changed scope, corrupt data
and original export/root identities. Receivers produce independent Git metadata
and preserve binary/new/renamed/deleted files in SHA-1 and SHA-256 repositories.
A lost checkout acknowledgement can recover its receipt, but incomplete evidence,
changed HEAD, a replacement destination or an existing folder never authorizes a
reset/replay. A highly compressible oversized tree stops before writing project
files; compressed artifact size alone is insufficient to bound disk consumption.

These cases qualify local mechanics and private protocol bounds. They do not
replace artifact-catalog authorization, controller fencing or the physical
OpenShell source→builder→verifier→lead workflow.

Cases: [Git and private handoff tests](../../harness/runtime/tests/coding_handoff_git.rs).

## Trusted source staging, publication and revocation

Real PostgreSQL tests pin application setup to the original team, lead, revision,
environment, active release/profile and artifact-store binding. Competing and
restarted controllers must preserve first-dispatch identity. Stale/expired claims,
changed settings and revoked original credentials reject. Immutable artifact
registration/publication uses the same catalog and byte-verification backend;
setup labels and generic API calls cannot grant publication authority. Source
artifact events have no fabricated model turn. Unknown upload/retry retains the
same export and artifact IDs; restoring a profile alone cannot revive work.

These tests establish transactional authority and restart behavior. Synthetic
bundle receipts in catalog cases do not claim Git validation; real Git and
OpenShell cases provide that separate evidence.

Cases: four `source_setup_*` tests in
[service contracts](../../harness/server-reference/src/service_team_types_tests.rs).
