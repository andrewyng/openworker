# Isolated coding source and handoff implementation

C4 implementation note. Status: preparing the implementation after C3 provider
qualification. This is not a statement that the isolated preset has shipped.

Related: [milestones and journal](CODING-AGENT-PRESETS-PLAN.md),
[coding variants](CODING-AGENT-PRESETS.md),
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
