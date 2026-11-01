# Isolated coding source and handoff implementation

C4 implementation note. Initial source staging, submitted-revision export,
independent verifier checkout, attributed reports, lead reception and explicit
integration passed the focused local service/OpenShell qualification. The complete
combined regression and final checkpoint are recorded in the linked journal.

Related: [milestones and journal](CODING-AGENT-PRESETS-PLAN.md),
[coding variants](CODING-AGENT-PRESETS.md),
[illustrated scenarios](CODING-WORKSPACE-SCENARIOS.md),
[placement lifecycle](TEAM-PLACEMENT-IMPLEMENTATION.md).

## What crosses the boundary

A child sandbox receives an independent Git checkout at an exact commit. It does
not receive the lead's linked worktree metadata, GitHub token, host directory or
artifact-store credentials. Committed source moves as a bounded Git bundle through
the existing immutable artifact store. Untracked/ignored workspace files are not
automatically uploaded. Exporting a source revision is an explicit application
configuration or authorized tool action, not a consequence of mentioning a path.

A handoff records:

- Repository identity supplied by the application, independent of transport URLs.
- Exact base and submitted commit IDs, including the Git object format.
- Original producer team/instance/session and operation identity.
- Verified artifact reference, byte count and digest.
- Receiver/import intent and its original destination workspace identity.
- Optional verification evidence bound to the exact submitted revision. Evidence
  is a report, not permission to execute tests or mark changes integrated.

Use a self-contained source bundle first. Incremental bundles may be introduced
later only with explicit prerequisite checks. Bundles preserve deletions, renames
and binary changes. Never substitute a text-only diff that drops those changes.

## Trusted setup and model actions

Application setup pins the source repository, base revision, allowed child profile
and artifact-store binding. Setup work has an explicit durable setup correlation;
it must not fabricate a model request or pretend the user approved a tool call.
The artifact catalog must distinguish that setup origin from ordinary tool-turn
outputs while retaining ownership, immutable publication and read authorization.

Prebuilt type defaults need an explicit application-owned sandbox/workspace/source
configuration. They are immutable retry inputs. Dynamic types continue using the
exact approved definition; a default or per-instance override cannot widen it.
An isolated preset supplies defaults for builders and verifiers without adding a
second team/orchestration system. A verifier receives the submitted artifact and
commit, rather than silently using the original base snapshot.

Model actions request export/import by registered repository or handoff identity.
They cannot provide a host path, arbitrary download URL, storage key or credential.
The harness records the intent before using ToolRunner. Pending actions may report
progress through the existing durable inbox/events; they do not need a polling
loop in the application. Importing data does not authorize running that project's
code. Tests/builds remain normal separately governed execution calls.

## Git and filesystem rules

Use the installed Git executable outside the project with a cleared environment,
disabled hooks, global/system configuration, fsmonitor, external diff, textconv,
credential helpers and recursive submodules. Validate repository pointers and
local configuration before inspecting source. Reject alternates, replacement
objects or external metadata that would silently enlarge the exported object set.
No network operation is needed for artifact handoff.

Git bundle export must name one exact revision, not `--all`. A raw commit ID is
not itself an advertised bundle ref. Use an operation-owned, namespaced temporary
ref pinned with an expected-old-ID check; record it before mutation and bundle
only that ref. Recovery inspects its original ref/marker rather than creating a
new one. Cleanup requires the same expected commit. This is an explicit local
Git-metadata write, not an unreported read-only operation; existing branch tips
must not change. The source configuration authorizes that bounded bookkeeping.

Validate bundle format, expected heads, complete prerequisite closure, object
integrity and the exact base/submitted commits. Treat Git objects and paths as
untrusted data. Import into a fresh independent checkout owned by a durable
operation; do not overwrite an existing directory or attach to a different repo.
A destination directory and its Git metadata get an identity receipt. Rechecking
that receipt is required before further import/integration work.

Keep manifests and chunks bounded by the existing artifact size limit. Stream
bounded chunks through the private ToolRunner connection; do not put a full
bundle into a model response or an unbounded JSON frame. Hash the bytes actually
transferred and verify the immutable artifact manifest before import. Artifact
publication and workspace preparation cannot become separate untracked uploads.

## Recovery and integration

Export, transfer, import and integration are separate durable operations. A lost
reply gives inspection authority over the original operation, not mutation retry
authority. Persist dispatch before I/O and inspect its original marker, artifact
and destination identity after recovery. An incomplete or replaced destination
stays explicit; do not wipe it and start over under the same identity.

A builder submits a particular commit. The verifier checks that commit. The lead
explicitly integrates reviewed changes, checking its expected current base and
handling conflicts. A changed integration base does not inherit earlier test
results. Publication to GitHub remains the opt-in C5 transport; no private key or
write access is needed for the artifact workflow.

## Build order and qualification

1. Portable handoff/operation contract and bounded Git bundle export/validation
   using real temporary repositories. Cover binary/deleted/renamed files,
   mismatched revisions, hostile config, corrupt bytes and missing prerequisites.
2. Immutable artifact origin/ownership and durable transfer/import intents using
   the existing storage adapter. Check concurrent publication, interrupted chunks,
   original IDs, authorization revocation and inspection-only recovery.
3. Application-owned prebuilt type defaults, independent checkout preparation,
   isolated preset helpers and corresponding Rust/TypeScript service APIs.
4. Actual OpenShell builder → separate verifier → lead integration, including a
   conflicting integration base and original-resource cleanup. Use the same local
   PostgreSQL/Kubernetes fixtures; rebuild the Linux runner only when its private
   handoff protocol changes. Record source/image identities and disk usage.

The grouped test catalog and journal must distinguish pure contract tests, real
Git/filesystem tests, storage/controller tests and physical sandbox qualification.
The independent-volume story and future sandbox-contained agent loop remain
separate from this milestone.

## Current transfer bounds

Private ToolRunner frames remain capped at 64 KiB; bundle chunks are 32 KiB.
Artifact bundles are capped at 64 MiB. Independent checkout additionally limits
expanded file content to 128 MiB and the tree to 10,000 entries (with a bounded
1 MiB tree listing). Submodule contents require a separately complete source;
checkout never initializes or downloads them. These v1 bounds reject explicitly
rather than dropping files or executing a repository-provided helper.

## Source setup outbox

The current application adapter records one immutable source configuration per
team: repository identity, relative source directory, Git object format and exact
commit. The configured server supplies the artifact-store binding. PostgreSQL pins
these to the original lead/environment/release/profile before dispatch. Setup IDs
are separate from LLM turns. A live controller claim and original credential are
required for reads and publication; setting an origin label is insufficient.

Source transfer runs in a bounded controller task, separate from normal control
reconciliation. Restart inspects the original export and resumes only immutable
same-ID artifact publication. Revocation blocks staging; an explicit application
retry after restoring authority retains the dispatch bit and original identities.
The public isolated preset and model tools use this same setup/import machinery;
setup alone does not fabricate a model call or its approval.

## Coding defaults and source choice

The shared runtime contract now has application-owned coding defaults per prebuilt
type. Builders select the initial pinned snapshot by default. Verifiers require an
explicit submitted handoff reference. A choice is only `initial` or a named
`submission`; it cannot carry an endpoint, storage key or credential. The catalog
still has to authorize that reference and verify its immutable artifact.

Default independent destinations are `checkouts/{team}/{instance}`, so identical
instance names in two teams do not collide. An explicit relative directory remains
possible. Linked worktrees cannot be combined with an independent source import.
Dynamic types do not inherit a prebuilt default by coincidence; explicit choices
still need the approved type's placement ceiling and original source authority.

Team configuration, reservation, trusted child-import outbox and SDK preset
helpers share this contract.
The required order is: original placement ready → selected artifact ready →
independent checkout receipt → child cwd/admission. A directory existing by itself
is insufficient evidence that it contains the selected source or Git metadata.
The import receipt must remain bound at launch; replacing `.git` cannot be
accepted as a newly discovered repository. Model submissions/reports and explicit
lead reception/integration use the same durable handoff/artifact records.

## Independent Git metadata at launch

An acknowledged import can produce the ordinary managed-workspace receipt used by
coding tools, bound to the same import operation/scope/destination. Its optional
independent-Git identity pins the checkout's own `.git` directory and grants no
external common-metadata exception. Later child commits are allowed; replacing
Git metadata, introducing `commondir`/alternates, or symlinking the object store
invalidates the receipt before coding tools run. Existing workspace receipts omit
the optional field and retain their prior serialized representation.

The private `handoff/workspace` conversion requires the original acknowledged
import, then `workspace/verify` checks its filesystem identities at launch. The
service import outbox must pin and acknowledge this receipt before child admission.
The method/receipt requires a matching ToolRunner image. Qualification records
the actual image digest; a compilation result does not establish admission safety.

## Root-repository bookkeeping

For a repository at the workspace root, first dispatch appends a fixed
`/.openworker/`, `/.openworker-received/` and `/.openworker-processes/` exclusions to its local Git `info/exclude`, after recording the
original operation. Existing patterns and tracked `.gitignore` remain unchanged.
The metadata file is bounded to 64 KiB and symlinks are rejected. Inspection does
not modify it. This keeps private transfer/worktree markers out of ordinary
`git add -A`; it is local bookkeeping, not a stronger sandbox boundary.

## Durable child-import outbox

A source-selected reservation is excluded from generic directory preparation. The
private import controller resolves the original published setup artifact and pins
the child's selected environment, activated profile digest, workspace request and
immutable transfer together before provider I/O. It commits separate first-dispatch
bits for import preparation and checkout. New controllers inspect original markers;
immutable chunks can resume, while checkout itself is never automatically repeated.
Artifact streaming consumes and checks the complete digest before checkout dispatch,
including bytes already present after a lost chunk acknowledgement.

Every frame and completion rechecks original credential, source/catalog ownership,
profile, environment binding and reservation. Completion writes the independent Git
receipt, source acknowledgement and outbox-ready state in one transaction. A child
cannot run before that commit. Retirement or revocation rejects late completion and
retains work. Unknown outcomes yield deduplicated lead-inbox notices. No downloaded
URL, object key or storage credential enters ToolRunner. Bounded import I/O runs in
a separate controller task so it cannot stall cancellation or cleanup.

The same import controller resolves either the initial source or an authorized
published submission. Public isolated presets require this adapter. Synthetic
storage tests are distinct from real Git and physical provider qualification.

## Submitted-revision model workflow

Keep the following effects separate and durable; model arguments select an
existing same-team object, never a filesystem root, storage key or credential:

```text
Builder                         Controller                    Lead / verifier
  commit in own checkout
  team_submit_code(commit) ---> export intent + original pin
  <--- submission ID, pending   export / inspect original
                               verify + publish artifact ---> inbox: ready + ID
                                                               |
                              +--------------------------------+
                              | team_spawn(...,
                              |   source: {submission, ID})
                              v
                       independent exact checkout ----------> verifier tests
                                                              reports for ID
                              +--------------------------------+
                              | team_receive_submission(ID)
                              v
                       original lead reception intent ------> inspect checkout
                              |
                              | team_integrate_submission(ID, expected_head)
                              v
                       bounded fast-forward-only action ---> explicit outcome
```

The submission records the actual producing team/member/session and originating
model call. Exact commit, source base, bundle receipt and immutable published
artifact form the handoff; a friendly summary is untrusted descriptive data.
Pending publication is not a completed handoff. Lifecycle inbox notices contain
identities/status rather than embedding model-authored text as harness authority.

Verification reports are separate immutable, attributed records referencing the
frozen manifest digest and submitted revision. Adding a report must not mutate
that manifest or invalidate another verifier's already-pinned import. A report is
an agent's claim, not a permission grant or automatic integration decision.
Verifiers receive the named submitted artifact, never the original source as a
fallback. Their qualification checks actual Git HEAD and test results.

The lead receives an independent inspection checkout before integration. First
provide fast-forward-only integration into the application's original source
repository, with a clean tracked tree, exact expected current HEAD and descendant
check. A moved base, divergent history or conflicting local state rejects clearly;
no force/reset or automatic conflict resolution. More involved merge/rebase work
is an explicit coding action and may produce a new submission. Do not mark a
report or reception as integrated merely because its bytes were imported.

Persist each original operation and its dispatch decision before provider I/O.
Lost replies permit inspection of the same export/import/integration; never repeat
checkout, merge or publication blindly. Unknown operations keep their workspace/
environment reference so retirement cannot discard work still needing inspection.
An operation proven never dispatched can be closed without allocating replacement
resources. Publication uses existing artifact ownership and storage bindings, with
an explicit submission origin rather than pretending a later controller still
owns the producing model turn.

Preserve transaction ordering. Controllers lock lead then team and operation data;
they must not introduce a producer-row lock after the team lock that can deadlock
a child tool transaction holding that producer row. Completion and inbox updates
remain atomic, original authority is rechecked on every frame, and audit append
comes last. Metadata/contract, real Git, fault-injected PostgreSQL and physical
OpenShell tests each qualify their own boundary before enabling SDK preset helpers.

## Current model-facing workflow and approval boundary

The application supplies `coding` defaults at service team creation; the server
supplies its configured artifact-store binding. Rust and TypeScript child options
carry an explicit `source` choice. A published submission can be consumed after its
producer retires, provided the original lead credential and immutable catalog
still authorize it. Reports stay separately attributed and never change a manifest.

```text
builder checkout / own .git
  | team_submit_code(commit, summary)       Ask / reviewer / bypass
  v
original submission intent -> controller export -> verified immutable artifact
  |                                                  |
  | ready notice in durable inbox                    | same submission ID
  v                                                  v
lead selects verifier                         independent verifier checkout
  |                                                  |
  |                                   team_report_verification(id, report)
  |                                                  |
  +---------------- attributed claims <--------------+
  |
  | team_receive_submission(id)            Ask / reviewer / bypass
  v
lead sandbox: .openworker-received/{team}/{submission} / own .git
  | inspect files; original source workspace is unchanged
  |
  | team_integrate_submission(id, expected_head)
  v
original source repo: ancestry + clean-tree checks -> index/files -> branch CAS
```

Mutating handoff calls pass through ordinary approval handling. Both storage
adapters recheck the exact saved approval and active session grant inside the
session transaction before committing a model's intent. Revocation after the
LLM-loop check therefore cannot authorize a queued export or integration. A stale
approval reopens the normal decision path. Trusted controller acknowledgements
are separate private operations; a model cannot choose artifact provenance labels.

The native Git integration does not execute hooks or content filters, use a remote,
or run repository code. It stages a local operation-owned bundle, requires the
submitted commit to descend from the current expected head, and updates the branch
with an expected-old-ID CAS. URL rewrite and sparse-checkout configuration reject.
Known stale/dirty/divergent rejections have durable negative receipts. Index/files
and ref updates are separate Git operations: a crash between them can leave a
partial outcome, which is retained for inspection. Inspection never repeats the
index update, resets files, changes the branch or writes a replacement receipt.

Root-repository bookkeeping excludes both `/.openworker/` and
`/.openworker-received/` locally. Existing tracked ignore files are unchanged.
This is staging hygiene, not kernel isolation or a way to hide already tracked data.

Public SDK usage: [isolated coding teams](ISOLATED-CODING-TEAMS.md).
