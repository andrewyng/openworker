# Isolated coding source and handoff implementation

C4 implementation note. Status: initial source staging, durable child import and
receipt-gated admission physically qualified; model handoff implementation and
full isolated preset qualification in progress.

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
The public isolated-preset/default and child-import admission integration is still
a separate gate; this adapter does not itself expose new agent tools.

## Next admission slice: coding defaults and source choice

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

Wire this contract atomically through team configuration, reservation and the
trusted child-import outbox before exposing the isolated preset in SDK helpers.
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
The newer method/receipt requires a matching ToolRunner image; the preceding
source/receiver physical evidence alone does not qualify this new admission path.

## Root-repository bookkeeping

For a repository at the workspace root, first dispatch appends a fixed
`/.openworker/` exclusion to its local Git `info/exclude`, after recording the
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

This slice resolves initial source only. Submitted-revision catalog lookup and
model-facing submission/reception tools are still required before public isolated
preset APIs are enabled. Synthetic storage tests are distinct from real Git and
physical provider qualification.
