# Coding agent presets, workspaces and GitHub publishing

Decision record: 31 October 2026. Records the agreed product direction and
recommended defaults. Preset names and configuration below describe the intended
interface; implemented entry points and platform qualification are documented in
[Coding agents](CODING-AGENTS.md). The isolated/publishing variants remain tracked
in the milestone journal until qualified.

Related: [team types and sandbox design](TEAM-TYPES-AND-SANDBOXES.md),
[team implementation ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md),
[coding implementation sequence and progress](CODING-AGENT-PRESETS-PLAN.md).

## Three coding presets

Provide versioned, customizable presets built on the existing agent, session,
tool, approval and team primitives. Keep one runtime and coordination model;
developers should not need to build their own orchestration for each variant.

| Preset | Behavior | Workspace and execution |
| --- | --- | --- |
| **Coding agent** | Handles the task directly. Dynamic helpers are optional when the application enables delegation. | Starts with one agent and one project workspace. |
| **Coding team — shared sandbox** | A dedicated lead coordinates prebuilt builder and verifier types, with optional custom types. | One sandbox; a separate worktree/branch for each builder and an integration worktree for the lead. |
| **Coding team — isolated sandboxes** | The same lead/builder/verifier workflow, with separate worker environments. | Each worker receives a checkout at a pinned base commit. Changes return through an explicit artifact or Git branch handoff. |

Recommended defaults:

- **Entry point:** the single coding agent. Selecting it does not start children.
- **When a team is requested:** the shared-sandbox preset with builder worktrees.
- **Handoff:** local commits for shared worktrees; patch/Git-bundle artifacts for
  isolated sandboxes.
- **GitHub publishing:** opt-in. When enabled, prefer the harness-managed publishing
  tool; sandbox CLI credentials require an explicit configuration choice.

The application can customize instructions, tools, default working models,
builder/verifier types, concurrency and budgets. Credentials and provider
connections stay outside preset bundles. Defaults must not silently replace an
unavailable model, workspace mode, publishing mode or sandbox placement.

## Roles, types and dynamic helpers

The lead decomposes work, coordinates members and reviews integration. Builders
make changes. Verifiers inspect the submitted revision and report evidence.
Verification permissions are explicit; a verifier label alone does not make a
process or its filesystem read-only.

A type is a reusable role; an instance is a particular worker. A prebuilt
`builder` type can produce `builder-ui`, `builder-api` and other named instances.
The lead may also define a session-private role with its own instructions,
allowed tools and permitted working model. Exact-definition approval and the
application's delegation ceiling apply to those custom types.

The single coding agent can use this same mechanism if dynamic helpers are
enabled. It then acts as their lead using the existing team machinery; there is
no second, less-restricted spawning mechanism. Helper placement must come from
the app's configured placement policy. Omitting delegation does not implicitly
grant permission to create helpers, publish code or provision new sandboxes.

## Shared sandbox: worktrees coordinate edits

```text
One sandbox / project repository
  Lead          → integration worktree / integration branch
  Builder UI    → builder-ui worktree / branch
  Builder API   → builder-api worktree / branch
  Verifier      → checks a specific submitted commit
```

Linked worktrees share the repository's Git objects, so the lead can inspect a
builder's commit without pushing it to GitHub. Each builder gets a distinct
working directory and branch to avoid accidental edit collisions. Agents must
also coordinate ports and shared resources.

Worktrees are not an access boundary. Processes in the same sandbox share its
aggregate filesystem, network and credential access. Restricting one agent's tool
list does not create an independent kernel-enforced sandbox for that agent.

Commit creation, verification and integration are explicit workflow operations.
The lead integrates reviewed changes and handles conflicts; spawning a member
does not automatically merge its edits. A verifier checks the submitted commit,
rather than an uncommitted working directory that a builder can keep changing.

### Child working directory and file-tool scope

For the shared-team preset, default each builder's working directory to its own
worktree, not the parent's directory. Default its read/write/search/patch scope
to that same worktree. Relative paths and shell commands start there. This is
the preset behavior built on the distributed child-workspace path qualified in
[C1](evidence/coding-presets-c1-20261031.json).

Keep these concepts distinct in configuration and enforcement:

| Concept | Purpose | Child default |
| --- | --- | --- |
| Project root | Identifies the overall repository and its managed worktrees. | Project identity inherited from the lead; not an automatic writable scope. |
| Working directory | Resolves relative paths and starts processes. | The child's assigned worktree. |
| File-tool scope | Bounds the paths accepted by read/write/search/patch tools. | The child's assigned worktree. |
| Git metadata access | Lets Git operate on the registered repository/worktree relationship. | The recorded worktree metadata and common Git metadata, within the shared sandbox. |

The file-tool boundary is useful protection against accidental cross-worker edits.
File tools continue to reject path traversal and symlink escapes; a metadata
exception for Git must not expand ordinary file access to the whole project.

Linked worktrees intentionally refer to Git metadata outside their working
directories. In the shared sandbox, authorized Git CLI commands can use that
metadata because the sandbox permits access to the shared repository. A working
directory does not contain a shell command or script: such processes may reach
other paths permitted by the sandbox. This is intentional shared-repository
access, not a security guarantee based on whether the harness notices an
indirect access.

Trusted Git tools can validate the recorded worktree, metadata paths and identities
before operating. They should admit only that known relationship, rather than
following arbitrary model-supplied `.git` pointers or allowing the entire parent
directory. The managed-worktree helpers qualified in C1 admit only this recorded metadata
relationship; other external metadata remains rejected. The
lead should inspect a submitted commit through explicit Git/handoff operations
instead of needing general write access to every child's worktree.

## Isolated sandboxes: explicit checkout and handoff

The trusted application/harness provisions the execution environments. Each
worker needs its own checkout or staged source snapshot at a recorded base commit.
Creating the sandbox alone neither mounts the application's host repository nor
clones GitHub. Repository staging and private-repository read access are separate
configuration from permission to publish changes.

Unlike linked worktrees in a shared sandbox, an isolated child gets an independent
checkout with its own Git metadata inside that sandbox. Do not mount only a
linked worktree whose `.git` pointer depends on inaccessible metadata in the
lead's sandbox, or silently mount the lead's metadata to make it work. Changes
cross the boundary through artifact or remote-branch handoff. The child still
uses its checkout as its default working directory and file-tool scope; the
sandbox separately enforces the stronger access boundary.

```text
Builder sandbox                 Verifier / lead environment
  Checkout pinned base
  Edit, test and commit
  Export changes ─────────────► Inspect exact submission
                                Verify and integrate
```

Use one handoff record for both transport options: repository identity, base
commit, submitted commit when available, artifact reference or remote branch with
its exact commit, and test/verification evidence. New files, deletions, renames
and binary changes must be represented; a plain text diff is not always enough.

The receiver validates the artifact and base before applying changes. A Git bundle
or a patch with the necessary metadata can carry changes without giving the child
GitHub write access. A pushed branch is an alternative transport. If the lead's
integration branch has moved, it handles conflicts and verifies the integrated
result rather than assuming the earlier verification still covers that result.

## Configurable GitHub publishing

| Mode | Execution | Credential location |
| --- | --- | --- |
| **Disabled** | Return local commits or handoff artifacts. | No publishing credential is needed. |
| **Harness-managed tool** | Child requests publication through a narrowly scoped trusted tool/service. | Credential stays in the trusted publishing domain, outside the child sandbox. |
| **Sandbox CLI** | Child runs `git push` using explicitly granted CLI access. | A scoped credential is available to processes in that sandbox. |

### Harness-managed tool: preferred publishing mode

```text
Child sandbox                     Trusted harness / publishing service
  Commit changes
  Export Git bundle ────────────► Validate artifact, repository, branch and commit
                                  Apply approval and access policy
                                  Publish using stored credentials
                    ◄─────────── Return branch URL and exact commit SHA
```

The child supplies an artifact reference, not an arbitrary path on the harness
host. For a remote sandbox, the trusted publisher must explicitly retrieve the
changes; it cannot assume access to the child's filesystem. It treats Git objects
and patches as untrusted data and does not execute repository hooks or code as
part of publication.

Enforce allowed repositories, branch prefixes and permitted operations in the
publisher, independently of model arguments and approvals. Publishing a branch
does not implicitly grant force-push, merge, repository administration or PR
creation. Create a new branch or check its expected remote tip before updating it;
an unexpected existing branch is a conflict, not permission to overwrite it.
Return structured results/errors and durable operation receipts without
exposing credentials. Use the normal approval modes and record activity.

This must be a genuinely trusted publishing operation. Running `git push` inside
the child sandbox with an injected token is the sandbox-CLI mode, even if a tool
wraps that command.

### Sandbox CLI: explicit credential access

Use controlled credential-helper or sandbox-secret delivery, with scoped and
short-lived credentials where the provider supports them. Do not embed tokens in
remote URLs or persistent Git configuration. Once exposed inside a shared sandbox,
a credential is potentially usable by other processes in that sandbox; it is not
private to a named agent instance.

Private-repository checkout and publishing are separate permissions. An application
can stage the source or grant read-only Git access while keeping all publishing
credentials with the harness. Revocation, refresh and supported secret delivery
must be enforced by the chosen provider, not merely described in the preset.

### Configuration sketch

This is illustrative configuration for the proposed presets, not runnable SDK
syntax. The application resolves connection/profile references before execution.

```typescript
const coding = {
  preset: "coding-team-shared",
  workspace: { project: projectDirectory, layout: "worktrees" },
  agentTypes: ["builder", "verifier"],
  dynamicTypes: { enabled: true }, // Subject to app-owned delegation limits.
  github: {
    connection: "github-work",   // Stored credential reference; never a token.
    publication: "harness-tool", // Or "sandbox-cli" / "disabled".
    repositories: ["company/product"],
    branchPrefixes: ["agents/"],
  },
};
```

For the single-agent preset, start without builders. For the isolated-team preset,
select authorized execution profiles and configure repository staging plus artifact
or branch handoff. The eventual API should reuse the existing agent/team and
RuntimeProfile abstractions; this sketch does not introduce a second policy model.
These choices are application configuration, not authority a child can expand.

## Recovery and implementation status

A lost publishing response is not retry permission. Preserve the original
operation ID and report an unknown outcome. Read the original remote branch/commit
before deciding whether another mutation is needed. Integration and publishing
remain separate from the worker's task-completion status.

Already qualified: session-private approved types, multiple named instances,
durable coordination and service-worker restart with real tools in the original
shared OpenShell sandbox. Local SDK child-directory/worktree preparation is also
tested. The recent service qualification shared one working directory; it did
not qualify remote builder worktrees or independent child sandboxes.

Still to implement/qualify: the packaged coding presets themselves, remote
worktree setup, isolated child checkout/provisioning, the handoff contract and
publisher, and full builder → verifier → lead integration scenarios. Generic
teams remain useful without Git; no clone, commit, merge, push or credential
grant becomes an implicit side effect of spawning a child.
