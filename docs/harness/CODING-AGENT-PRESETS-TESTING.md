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

## Sandbox Git credentials through OpenShell

The native adapter/ToolRunner slice has three test layers. Fast scope tests check
exact HTTPS host/repository matching, malformed inputs, raw-key refusal, original
provider/profile pins and exact OpenShell 0.1.3 protobuf wire compatibility. Cleanup
tests separate execution revocation from permission to remove the original owned
sandbox; replacement UID, image, ownership or workspace rejects.

Disposable Linux process tests run the real Rust Git helper and shell/process
manager. They check default secret stripping, explicit placeholder-only opt-in,
per-command credential references and no persistent Git key/helper. These establish
our transport behavior, not OpenShell's actual credential rewriting.

The local Kubernetes fixture sends real Git clone/fetch/push traffic over verified
HTTPS through a real OpenShell supervisor to a synthetic-token Git server. Read
and push profiles differ. Unconfigured repositories reject; expiry waits for the
**original supervisor revision acknowledgment** before checking denial; restored
validity works on a new tool connection through the same process manager. Detach
blocks existing Git access and new attachment while original-resource cleanup
still succeeds. This is not a live GitHub write or external OAuth refresh test.

Cases: [scope and credential helper](../../harness/runtime/src/coding/openshell_git.rs),
[original provider/cleanup pins](../../harness/runtime/src/environment_openshell.rs),
[Linux process tests](../../harness/runtime/tests/openshell_git.rs),
[native live probe](../../harness/runtime/examples/openshell_git_probe.rs), and
[fixture procedure and expiry-contract tests](../../harness/runtime/tests/fixtures/openshell-git/README.md).
The service/SDK layer additionally checks named operator registry selection,
scope-bound environment identity, exact grant sets, same-grant-or-none delegation,
rejection of raw secret/config-selection fields and immutable installation pins.
The real PostgreSQL/API case prepares a team in its original granted environment.
Rust and TypeScript register profiles and execute real Git through service workers.
A shared member inherits its sandbox's credential capability, a separate
credential-free private type cannot fetch, an independently authorized write
profile can publish, and cross-grant child delegation is forbidden. Restart keeps
the original session/environment. The default combined suite also reruns C1–C4.

Cases: [operator registry](../../harness/server-reference/src/git_access.rs),
[PostgreSQL placement/API cases](../../harness/server-reference/src/service_team_placements_tests.rs),
[service Git layer](../../harness/server-reference/tests/support/service_git.rs),
[TypeScript transport](../../harness/typescript-sdk/test/service.test.mjs),
[placement helper](../../harness/typescript-sdk/test/team-placement.test.mjs),
[Helm schema](../../deploy/helm/chart.test.mjs) and
[local container configuration](../../deploy/service/local-values.test.mjs).
[Configuration guide](OPENSHELL-GIT-ACCESS.md).
The C5 trusted publisher is a different, still-pending transport.

The Linux run also protects an editing-lock regression unrelated to credentials:
an inherited/duplicated FD must not retain a completed edit's flock, and closing
that old FD must not release the next owner's lock. This deterministic test uses
no sleeps or process timing; see the lock tests in
[coding files](../../harness/runtime/src/coding/files.rs).

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

## Isolated coding defaults and submitted-revision verification

Three contract cases check stable per-team/per-instance destinations, explicit
relative overrides, registered prebuilt types and pinned initial source revisions.
A verifier must receive a submitted-handoff reference and cannot fall back to the
initial snapshot. Linked Git metadata, reserved harness paths and host/traversal
paths reject. These are resolution tests; they grant no sandbox or artifact access
and do not replace the later catalog/admission qualification.

Cases: [isolated-default contracts](../../harness/runtime/tests/coding_handoff_config.rs).

## Imported Git identity through tool launch

Real filesystem tests convert an acknowledged independent import to a managed
workspace receipt and run ordinary coding tools through it. Child commits can
advance HEAD. Replacing `.git`, adding external common/object metadata or mixing
linked-worktree exceptions rejects before tool execution, including ordinary file
reads. Private subprocess tests check conversion and subsequent workspace
verification over the actual stdio channel. They also cover macOS root aliases:
all private operations must agree on one canonical root identity.

Cases: `imported_workspace_pins_own_git_metadata_across_child_commits_and_launch_checks`,
`independent_workspace_cannot_acquire_external_git_objects_or_linked_metadata`
and the private-stdio case in
[Git handoff contracts](../../harness/runtime/tests/coding_handoff_git.rs).

## Reservation and admission parity across storage adapters

The same synthetic source/default contract runs against SQLite and PostgreSQL.
It verifies deterministic reservation paths and source choices, immutable retries,
explicit submitted-revision selection, and rejection of unqualified adapters.
Directory acknowledgement alone cannot admit a source-selected child. A private
matching manifest/import receipt is required; ordinary API commands cannot forge
that acknowledgement. These are transaction/data contracts, with synthetic
filesystem identities; actual Git identity checks belong to the filesystem layer.

PostgreSQL service tests additionally prevent the generic workspace controller
from consuming source-selected children, keep incomplete imports out of admission,
and close unavailable templates without starting a child. An audit failure rolls
back configuration, templates, membership, source setup and source events together.
Failure notices persist in the lead inbox and deduplicate repeated reconciliation.

Cases: `exercise_coding_defaults` in
[shared storage contract](../../harness/tests/agent_team_contract.rs),
and the two `coding_*` cases in
[service contracts](../../harness/server-reference/src/service_team_types_tests.rs).

## Durable child-source admission and recovery

Real PostgreSQL transactions test one preparation dispatch, one checkout dispatch,
controller replacement, stale claim rejection, original artifact/environment pins,
revoked credentials, no automatic revival, atomic receipt acknowledgement, and
retirement before a late result. An injected completion failure rolls back both
source and workspace receipts; observer reads do not claim the model worker lease.
Pre-dispatch denial closes only an unstarted reservation and leaves reconciliation
working for other teams. These fixtures use synthetic bytes/identities to test
storage authority, not Git correctness or physical isolation.

Cases: [child-import contracts](../../harness/server-reference/src/service_team_imports_tests.rs).
Physical source/import tests use the real controller, artifact store and ToolRunner
with independently provisioned OpenShell sandboxes in
[service qualification](../../harness/server-reference/tests/service_v2.rs). Their
actual run identity/result is recorded separately in the milestone journal.

The private-controller fault-injection peer loses replies after storing a chunk
or creating the checkout. Recovery uses the original intent and inspection call;
counts prove `prepare`, chunk write and checkout each happened once. Child admission
remains blocked until the recovered receipt commits. Real artifact bytes travel
through the filesystem store; the peer's Git identity is synthetic, so this test
does not stand in for the separate Git/OpenShell qualification.


## Submitted revisions and verification evidence (C4)

Portable tests bind each submission to its original team, producer, acknowledged
source, repository, object format and exact commit. A different call ID or summary
cannot create another export of that revision. Verifier reports require an
acknowledged import of that exact manifest; they remain attributed claims. Foreign
team IDs and unimported revisions reject before reserving a child.

Real PostgreSQL tests cover dispatch leases, stale owners, inspect-only recovery,
artifact registration/publication gates, identity substitution, revocation,
reception and integration outboxes, and retention of unresolved original work.
Private setup/submission artifact origins cannot be forged through public APIs.
Rust and TypeScript transport tests cover coding defaults, selected-submission
spawn, catalog pagination and delegated-session resume identity.

These checks prevent a report, unverified upload or changed identity from being
mistaken for verified source. They do not establish that model-written reports
are truthful; physical tests separately assert actual exit codes and file output.

Cases: [portable handoff contract](../../harness/runtime/tests/coding_handoff.rs),
[PostgreSQL handoff cases](../../harness/server-reference/src/service_team_imports_tests.rs),
[TypeScript service contract](../../harness/typescript-sdk/test/service.test.mjs).

## Explicit integration and interruption (C4)

Real temporary Git repositories cover SHA-1 and SHA-256, binary files, deletion,
rename, stale expected HEAD, dirty files, divergent history and URL rewrite
rejection. Integration fast-forwards only an explicit expected branch head.
Known rejections are durable. Lost receipts can be recovered by inspecting the
original branch/tree. A simulated partial index/files change with the old branch
head stays unknown; inspection must not repeat read-tree, reset files or merge.

Approval tests check Ask and uncertain AutoApprove before any handoff effect, and
revoke an Allow Always grant between initial authorization and intent commit.
The latter must reopen approval without inserting an export/integration intent.
Bypass still uses the original source, placement and artifact restrictions.

These checks protect current work and prevent a network failure from becoming a
blindly repeated mutation. The branch CAS and index/files update are separate Git
operations; the tests deliberately do not claim atomicity across them.

Cases: [native Git handoff tests](../../harness/runtime/tests/coding_handoff_git.rs),
[approval transaction tests](../../harness/runtime/src/runtime_policy_tests.rs).

## Complete artifact-first service workflow (C4)

The local OpenShell fixture submits an actual builder commit, exports/uploads the
bundle, deduplicates a new-call retry, provisions a distinct verifier sandbox,
and checks actual Git HEAD and file bytes before recording its report. The lead
receives the same artifact into a separate directory. A stale expected-head
integration is rejected without changing the source; a correctly pinned request
integrates and is checked through actual source reads. TypeScript resumes the
lead and starts a second exact-submission verifier in another OpenShell sandbox.

The focused mode is `OW_HANDOFF_ONLY=true`; the default runs this alongside the
existing worktree, type, placement, recovery and SDK checks. Both use real service
processes, PostgreSQL, controllers, worker and digest-pinned Linux ToolRunner.
They require no cloud resource, GitHub credential or live model. Sandboxes and
owned schemas are cleaned even when a test assertion fails.

Cases: [physical workflow](../../harness/server-reference/tests/support/service_handoff.rs),
[combined entry point](../../harness/server-reference/tests/service_v2.rs),
[TypeScript continuation](../../harness/typescript-sdk/test/service-v2-live.mjs).

## Process-manager addressing in long workspaces

Linux unit tests check bounded abstract Unix addresses, original-directory and
UID scoping, and changed endpoint identity after directory replacement. A real
MCP test starts, reconnects to and stops a process under a deliberately long
workspace path. The private directory lock and peer-UID checks remain enforced.
Short filesystem socket paths retain their existing behavior.

This guards against valid nested checkout names silently disabling process tools.
The regression was found by the physical verifier test; shortening its name would
have hidden a production defect. Run the real manager test only in the disposable
Linux fixture, which owns its processes.

## Registered native tools alongside sandbox tools

The v3 selection regressions check exact fully qualified names, versioned registry
identity and release/type subsets. Remote coding MCPs stay in ToolRunner; explicitly
compiled native callbacks run on the trusted host. Unregistered callbacks, implicit
external executables, HTTP bindings and local file/credential grants fail closed in
this remote profile. Stored placement/registry fingerprints are rechecked when
attaching and when a child selects another environment.

This ensures a RuntimeProfile grant cannot accidentally expose an unselected tool,
and that a trusted callback is never described as kernel-sandboxed. Native callbacks
must interpret remote workspace paths as logical paths, not host filesystem grants.

Cases: [registered selection](../../harness/runtime/tests/registered_selection.rs)
and [publication registry/profile checks](../../harness/server-reference/src/repository_access.rs).

## Trusted repository staging and constrained publication

Real temporary Git repositories exercise pinned source reads, SHA-1/SHA-256,
create-only publication, expected-head fast-forward CAS, dirty/stale/divergent
inputs, corrupted artifacts, expiry/revocation and branch/repository denial. Lost
push acknowledgements and controller cache loss permit original-ref inspection,
without another push. A final authorization check runs immediately before mutation.
The HTTPS fixture additionally checks real Git HTTP, verified TLS, an ephemeral
synthetic header, denied authentication and receive-pack counts after inspection.

Real PostgreSQL cases verify one durable publication identity across new call IDs,
claim/epoch fences, credential revocation, cancellation, immutable manifest/proof
bindings and outcome notices. The native callback must have its exact current
ordinary tool approval; revoked session grants reject inside the intent transaction.
Source setup requires app-owned scope/ref/commit, retains prepare/checkout dispatch
bits across replacement, rejects stale receipts and blocks the lead's first turn
until its original checkout is acknowledged. These PG fixtures use synthetic Git
bytes and do not substitute for real Git validation.

The optional physical service case combines private staging, verified artifact
storage, a fresh lead checkout, independent builder/verifier sandboxes, attributed
exact-revision reports, explicit integration and registered native publication.
A controlled file remote proves service wiring; the separate HTTPS case proves
auth transport. Neither makes an unapproved write to a real GitHub repository.

Cases: [broker Git and TLS tests](../../harness/git-broker/src/tests.rs),
[PG/controller fault cases](../../harness/server-reference/src/service_team_imports_tests.rs),
and [physical trusted workflow](../../harness/server-reference/tests/support/service_broker.rs).

## Parallel teams, retirement and bounded live-model work

The mixed-placement case creates two teams, four builders and four readers.
Builders run in both their lead's sandbox and independent child sandboxes. A worker
is replaced before initial execution; database audit intervals must show all four
tool calls overlapping. Each reader shares its particular builder's environment
and checks that builder's exact output. Team identities, different directories,
closed-member admission and retained work are checked independently.

Finish-current retirement pins the original turn so an already accepted handoff
can settle; it does not accept new work or later turns. Cancel retirement refuses
undispatched effects. Existing quota, approval, budget, provider replacement and
lost-chunk/checkout tests remain part of the real-PG layer.

The explicitly enabled live-model case asks a lead to create a builder and an
exact-submission verifier for a tiny single-file counter app. It independently
checks the published artifact's checksum, Git integrity and actual committed HTML,
plus recorded verifier file reads. Reports alone are not treated as proof. Calls,
output and wall time are bounded. No model key enters ToolRunner, and no model
request can publish, merge or create a PR as part of that test.

Cases: [mixed teams](../../harness/server-reference/tests/support/service_parallel.rs),
[live team](../../harness/server-reference/tests/support/service_live_team.rs),
and the PG retirement/approval cases linked above. Consult the journal for which
cases and platforms have actually passed; definitions alone are not qualification.
