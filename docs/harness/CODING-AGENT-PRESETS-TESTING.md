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

## Later milestones

Preset construction, delegated isolated placement, artifact handoff, constrained
GitHub publishing, CLI credentials and the combined multi-team recovery matrix
are tracked separately as C2–C7. Passing C1 does not qualify those features.
