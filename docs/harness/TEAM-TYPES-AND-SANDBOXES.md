# Agent types, team management and sandbox placement

Decision record: 31 October 2026. Accepted in discussion with Rohit.
Implementation tracking: [plan and ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md).
Baseline: `f13de9b9`, public branch `openworker-harness-sdk`.

This document separates the team implementation we are extending now from a
future integration in which the OpenWorker agent loop itself runs inside an
OpenShell sandbox. The future integration must not block the current work.

## Baseline before this extension

The first-class team API distinguishes a registered agent **type** (`builder`)
from named **instances** (`builder-ui`, `builder-api`). A lead can spawn several
instances of a registered type. Each has its own durable session, conversation
and inbox. SQLite and PostgreSQL implement the same Rust coordination domain.

The model tools are `team_spawn`, `team_wait_agent`, `team_post_board`,
`team_read_board` and optional `team_send_message`. Only mentions notify board
participants. Direct messages stay off the board. Inbox input arrives before the
next model request; idle recipients wake. Durable waits release worker capacity.
Spawn and application command receipts prevent duplicate instances/effects after
response loss. A team stop cancels its members and preserves history.

Rust embedded/managed SDKs, TypeScript managed stdio and the reference service
expose these operations. The service pins approved release definitions and child
session templates. Its controller reconciles reservations and queues initial
tasks; its worker pool executes the existing runtime. Working-generation budgets
aggregate across team members, alongside existing per-turn limits. Compaction
and reviewer calls currently remain under their existing per-turn allowances.

Current service teams share one original OpenShell environment. The trusted
controller creates it through the gateway, outside the sandbox; ToolRunner runs
sandboxed coding operations. At that baseline there were no model-defined session-local types,
per-member sandbox choices or individual retirement tools yet. Application-driven
`agent/register` is harness-wide and must not be exposed directly as the new
model-facing definition tool. Legacy `.subagent()`/work-item teams and Python's
staffing proposals retain their separate contracts.

Sources: runtime `agent_team.rs` and `team_coordination.rs`, SQLite
`sqlite_agent_team.rs`, PostgreSQL `agent_team.rs`, SDK `host.rs`/`host_runs.rs`,
and service `agent_teams.rs`/`management.rs`.

### SDK scoped-type implementation (31 October)

The feature branch now implements an opt-in custom-type catalog for the Rust
embedded/managed SDK and TypeScript managed SDK, using either SQLite or PostgreSQL.
The application supplies a delegation ceiling when creating the team; without it,
the model does not receive `team_define_type`. Custom types currently share the
team's existing environment. Other placement requests reject at configuration;
the distributed service's release-backed adapter and isolated provisioning remain
in the next milestone. This is not a claim that every provider is qualified.

```typescript
const team = await harness.newTeam("lead", {
  workspace: projectDirectory,
  agentTypes: ["builder", "verifier"],
  delegation: {
    tools: ["coding.read_file", "coding.search_files"],
    // Optional model choices use the same provider/name shape as agents.
    // models: [{ provider: "OpenAI", name: "your-model-id" }],
  },
});
```

An exact approved definition is reusable by name in this lead session. Editing it
creates another immutable revision requiring another decision. Existing children
keep their old revision; new children use the latest approved revision. A denied
revision does not delete an earlier approved revision. A cancelled pending
proposal is abandoned before another turn can propose a replacement.

Receipts and type-to-child pins commit in the same database transaction as the
tool result. Restart reconstructs private bundles before adopting saved sessions;
they never enter the global app catalog. Whole-tool session grants and SDK
`interrupt_on` exclusions cannot approve arbitrary new type definitions. Ask uses
an exact approval, Auto-approve uncertainty pauses for a human, and Bypass still
enforces the delegated tool/model/policy ceiling. Decisions have tool/policy spans
and a metadata-only `team_type_decision` event. Ordinary child tool approvals keep
their normal behavior. This first adapter requires at least one delegated tool.

## Story 1: trusted harness outside the sandbox — implement now

```text
Application: lead policy, registered types, delegation ceiling
                             |
                             v
Trusted OpenWorker harness / service
  Lead and child agent loops, type registry, approvals, durable coordination
                             |
                    Existing OpenShell gateway API
                             |
              +--------------+--------------+
              |                             |
       Shared sandbox                Separate child sandbox
       ToolRunner                    ToolRunner
```

The model requests work; trusted infrastructure validates, authorizes, records and
provisions it. The application can run a lead with only team tools while allowing
it to delegate a defined catalog of worker tools. Those delegable tools do not
automatically become tools the lead can execute itself.

Sandbox settings cannot exceed the lead session's application-authorized ceiling.
Pre-built types and custom types both obey this boundary. Bypass skips human
approval, not access checks, isolation requirements, resource quotas or ownership.

## Pre-built and session-defined types

Pre-built types supply application-owned instructions and allowed settings.
Custom types are created by the lead using a separate `team_define_type` operation:

```javascript
team_define_type({
  name: "docs-reviewer",
  instructions: "Review documentation for inconsistencies.",
  tools: ["read_file", "search_files"],
  sandbox: {
    mode: "new", profile: "review",
    files: ["project-read"], network: [], credentials: []
  }
});
team_spawn({
  agent_type: "docs-reviewer", agent_id: "docs-reviewer-2",
  message: "Review the installation guide."
});
```

These are the target API shapes, not a claim that the new tools are shipped.

- A custom type belongs to one lead session. It survives that session's restart
  but is not visible in another lead's type catalog or the global app registry.
- Only the lead may define types and spawn instances. Child instructions are
  model-authored task configuration, not a new permission grant or human approval.
- Names cannot shadow pre-built types. An approved revision is immutable; edits
  create a new explicitly reviewed revision. Existing children keep their revision.
- Type identity/approval binds instructions, tools, sandbox envelope and any
  permitted model selection. Credentials are references, never values in prompts.
- Tools, file/network/credential grants, approved RuntimeProfiles and CPU/memory
  must fit the lead's delegation ceiling. Reject excess access rather than silently
  broadening or silently changing the requested configuration. Revalidate at spawn.
- A type cannot select a weaker approval mode or replace the trusted reviewer.
  The app owns these settings; ordinary child tool approvals still apply.
- Scope resolution is server-owned. A request cannot name another lead's session
  to borrow its private types, approvals, environments or credentials.

Approval is a durable definition operation. In Ask mode, show a card naming the
type, selected tools/access, placement and resources, with instructions/details
under expansion. Approval means **reuse this definition in this session**; it is
not blanket approval for other definitions or all future tool calls. Auto-approve
reviews the same concrete definition and asks the human when uncertain. Bypass
creates it without a human pause after the same hard validation. No child or
sandbox is created while definition approval is pending.

## Child briefing, context and workspace

Accepted after comparing the current SDK, a partner's session-creation screenshots
and the collaboration tools available in our development session:

- Keep `agent_type`, stable `agent_id` and per-instance `message`. Add an optional
  human-readable `title` and `background` briefing. Background contains project
  facts, not system policy or evidence of human authorization. Type instructions,
  briefing and the current task remain separate and source-labelled.
- Default to fresh child history. Explicit `context: { mode: "fresh" }` has the
  same meaning. A supplied background briefing is the first supported way to pass
  context; inherited conversation requires an explicit, bounded snapshot with
  source/provenance and secret filtering. Until that adapter is qualified, reject
  snapshot/inheritance requests rather than silently forking or dropping context.
- Workspace selection is independent of sandbox placement. Omission inherits the
  team workspace. An explicit authorized relative directory or managed Git
  worktree/branch may be selected. Resolve paths inside the selected environment,
  reject escapes/symlink escapes, and do not accept arbitrary host roots. A
  worktree separates checkouts, not permissions, network, processes or ports.
- Pin the resolved title, briefing/context choice, workspace, branch/worktree
  receipt, type revision, model, parent and environment identity with the child.
  All supplied options participate in spawn idempotency; changed retries reject.
- Optional model overrides must fit the app-owned model delegation ceiling.
  Credentials and approver selection remain host-owned. Unsupported reasoning
  options reject; never silently select another model or weaker approval mode.
- The harness derives initiation/parent identity for audit. Model-supplied
  `user_asked` or similar assertions never authorize an action.

Lifecycle controls distinguish interrupt/cancel-current-turn, sending follow-up
work, and retiring a member. Cancellation does not erase the child; retirement
blocks new work and automatic wakes. Listing/state expose these distinctions.
Messages still wake idle agents and reach busy agents before the next model call;
pending human approvals and cancelled turns are not implicitly approved/resumed.

Title, background and explicit fresh context are implemented in the common spawn
contract and both SDKs. Workspace/worktree selection, inherited snapshots,
per-instance model overrides and separate sandbox placement remain subsequent
adapter work. Unsupported fields/modes reject; they are not silently ignored.

```typescript
await team.spawn("build-report", "builder", "builder-report", "Build the report page.", {
  title: "Report builder",
  background: "The existing app uses React and the shared report API.",
  context: { mode: "fresh" },
});
```

Rust exposes the same fields with `spawn_with_options(..., ChildOptions { ... })`;
ordinary `spawn(...)` still uses defaults. Service clients use the same shape.
The harness stores the title as the child's display name while preserving its
stable instance ID. Briefing/task fields enter the initial conversation as
separate structured collaborator data, not another system prompt or copied
history. Identical spawn retries reuse the child; changed titles/briefings reject.
Combined encoded briefing/task size is bounded before reserving the instance.

## Sandbox placement and ownership

`team_spawn` retains its existing type/instance/task arguments and accepts a
sandbox selection within the approved type envelope:

| Selection | Meaning |
| --- | --- |
| Omitted / `mode: "team"` | Use the team's existing environment. |
| `mode: "member", agent_id: "builder-ui"` | Attach to that member's existing authorized environment. |
| `mode: "new"` | Provision an independent environment with selected approved settings. |

An agent cannot invent raw host paths, endpoints, secrets or cloud identities.
Named grants resolve through trusted application configuration. Resource and
concurrency quotas cover pending provisioning as well as ready environments.

**A shared sandbox has shared underlying access.** Smaller tool lists alone do
not make filesystem or network isolation narrower for arbitrary shell execution.
Sharing requires compatible effective sandbox policies; otherwise use a separate
sandbox or reject the request. Separate sandboxes may mount the same project with
different access only when the provider supports and enforces that storage model.
Unsupported shared volumes, mounts or policy restrictions must reject clearly;
there is no host-execution or broader-policy fallback. Initial isolated scenarios
can use independent workspaces; shared-storage verification is a distinct provider
qualification, not something inferred from a shared path string.

The controller persists the original environment ID, provider reference and
provisioning request before dispatch. Retries inspect the original object after an
unknown outcome; they do not create another sandbox or switch creation paths.
Every instance pins its type revision, resolved permissions and environment.

## Dynamic membership

`team_spawn` already adds instances after the initial team is created. Extend that
same operation for custom types and sandbox choices. Add `team_manage` with:

- `action: "list"`: bounded, paginated roster with status, type revision and
  environment identity; private definition content remains separately authorized.
- `action: "retire", agent_id, mode: "finish_current"`: default graceful removal;
  stop new admissions and automatic wakes, finish current work, then retire.
- `action: "retire", agent_id, mode: "cancel"`: explicitly request cancellation
  through the normal control path, preserving unknown-effect semantics.

Retirement frees active capacity only after work/resources settle. IDs, history,
receipts and audit evidence remain reserved; a replacement gets a new instance ID.
Pending/retiring instances count against admission limits. Historical retention
has a separate bound so repeated replacements cannot grow metadata without limit.
Waiters receive an explicit retired/cancelled result. Retirement does not delete
the shared project or another member's sandbox. Owned exclusive environments may
be released after their final attachment and all admitted work have settled.

## Story 2: agent loops inside sandboxes — future RFC integration

```text
Root OpenShell sandbox: OpenWorker lead loop, team-building tools
                             |
                   openshell.local/mcp
                             |
          Supervisor identity -> OpenShell gateway
                             |
              +--------------+--------------+
              |                             |
       Child sandbox                 Child sandbox
       OpenWorker child loop         OpenWorker child loop
       and ToolRunner                and ToolRunner
```

This is a distinct later deployment/reference example. The lead and child loops
run inside their sandboxes; the lead actually invokes OpenShell's sandbox-local
MCP interface. The external trusted gateway/operator remains the authority for
sandbox policy. Moving the loop does not grant it control-plane credentials or
make model-authored instructions trusted authorization.

Reference: [OpenShell PR 4721 / RFC 0021](https://github.com/NVIDIA/OpenShell/pull/4721),
reviewed at head `c5bd6e5b8bc02672145c2d5cb8e3275a6a6bd3fd`.
That revision proposes one level of children, copied/synchronized parent policy,
and inherited image/resources/providers for sandbox callers. Narrower child
policies and approved template choices are future work. The RFC is not a released
dependency. Keep those capability restrictions specific to that adapter; do not
block today's trusted-gateway implementation or claim MCP v0 supports subsets.

The two stories share type/instance identity, inbox/board semantics, approvals,
durable receipts and SDK concepts. They have different execution placement and
provider capabilities. Record the selected placement/provisioner; never silently
move a sandbox-contained loop outside on failure.

## Implementation and qualification

Follow the [milestone ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md). Keep the new tools
unadvertised until their policy, approval, storage and execution paths are wired.
Reuse the existing Rust coordination/storage/runtime rather than creating another
agent loop. Add metadata-only telemetry for definition/provisioning/retirement
decisions with stable session/type/instance/environment correlation; do not export
system prompts, secret values or raw file/tool payloads by default.

Test two concurrent teams, multiple instances, scoped-type isolation, permissions
and approval modes, mixed placement, parallel file/port coordination, retirement,
crash recovery, unknown outcomes, cleanup and quotas. Deterministic store/transport
tests precede real local Kubernetes/OpenShell and bounded live-model qualification.
The existing shared-team suite remains a regression gate. Cloud trials and the
sandbox-contained RFC demonstration remain separate qualifications.

## Qualified child workspace API (M3b)

For SDK-hosted teams with explicit sandbox `off`, a child may select an existing
relative directory or a managed Git worktree under the lead's authorized project:

```typescript
await team.spawn("ui-v1", "builder", "builder-ui", "Build the UI", {
  workspace: { mode: "worktree", repository: "source", path: "workers/ui", branch: "builder-ui" },
});
```

```rust,ignore
team.spawn_with_options("ui-v1", "builder", "builder-ui", "Build the UI", ChildOptions {
    workspace: ChildWorkspace::Directory { path: "frontend".into() },
    ..Default::default()
}).await?;
```

Omission uses the original team workspace. A worktree uses an existing local commit
(`HEAD` by default), requires installed Git, and never fetches or installs software.
Directory/worktree choices change the working location, not host permissions or
network access. Git inspection tools retain their existing stricter metadata scope;
linked worktrees can use file tools and explicitly authorized shell commands.

Preparation has a durable one-shot dispatch receipt and original directory/Git
metadata identities. After response loss, recovery inspects the same operation;
it never repeats `git worktree add`. An absent/replaced location or uncertain result
stops that child before task admission and reports the error to team waiters.
Successful preparation survives SDK restart. Worktree files remain user work and
are not deleted implicitly. Remote service selection stays rejected until its
ToolRunner preparation and enforcement path is qualified; the internal framed
`workspace-stdio` helper alone is not a claim of remote support.

## Qualified member lifecycle API (M4a)

The lead can call `team_manage` to list a page of members or retire one instance.
Applications use `team.members({ limit, after })` / Rust `members(after, limit)` and
`team.retire(idempotencyKey, agentId, mode)`. Omitted mode is `finish_current`.
The API accepts 1–100 entries per page; IDs remain stable even after retirement.

- `finish_current` blocks new turns, mentions and automatic inbox wakes. An existing
  turn can finish, including resuming a parked team wait or answering a pending
  approval. Unconsumed collaborator messages do not become another turn.
- `cancel` requests the existing turn-cancellation path. Tool outcomes already
  recorded as unknown stay unknown; cancellation does not replay those actions or
  claim that external effects were rolled back.
- The member stays `retiring` and counts against active team capacity until its
  owning runtime has closed the session and settled cleanup. Pending cleanup is
  visible in the roster. Shared projects/environments are never deleted by this
  operation. Exclusive environment ownership/release follows the M3 placement work.

Retirement is permanent for an instance ID. A replacement gets a new ID. Active
capacity is configurable; retained instance history has a separate 4,096-ID bound.
Command receipts retain the original response across retries and restarts. A
retirement mode is pinned; to interrupt current work without retiring an agent,
use the existing child-session turn-cancellation control, then send follow-up work
explicitly. Cancelling a turn and retiring a member have different admission rules.

The distributed controller cancels unstarted queued follow-ups; owning workers
perform runtime cleanup. Retiring a reservation that never became a session does
not create that session just to close it. Read-only API principals can list only
teams within their authorized scope and cannot turn the list endpoint into a
retirement mutation.

A retired child remains in the original run's durable event replay set. Slow or
reconnecting clients receive its final events even after it leaves the live roster.
Roster pages and wait notices carry the last turn outcome and a conservative
`outcome_unknown` flag; closing the agent is not evidence that uncertain external
changes were rolled back. Saved API controls retain priority during retirement.

## Qualified per-child working model selection (M3c)

SDK teams may override the working model for a spawn when the application has
supplied that exact provider/model pair in `delegation.models`. This works with
SQLite or PostgreSQL, embedded Rust or a managed child process. Omission keeps the
registered type's default model. The approver's model is configured separately.

```typescript
const team = await harness.newTeam("lead", {
  workspace: projectDirectory,
  agentTypes: ["builder"],
  delegation: {
    tools: ["coding.read_file"],
    models: [{ provider: "Local", name: "builder-model" }],
  },
});
await team.spawn("build-ui-v1", "builder", "builder-ui", "Build the UI", {
  model: { provider: "Local", name: "builder-model" },
});
```

Rust uses `ChildOptions { model: Some(Model::new("Local", "builder-model")),
..Default::default() }`. The provider must already be configured on the harness;
credentials and endpoint configuration are not part of the child arguments.
Internal model tools use the existing `{ connection, model }` wire shape. Validation
checks exact pairs, not a cross-product of allowed providers and model names.

The full spawn request pins the override; retries cannot change it. The resolved
selection is saved on the child session and survives restart. If its provider is
unavailable before session admission, that reservation closes with an explicit
configuration failure: no child task starts and no other model is substituted.
The host remains usable. Fixing configuration does not replay a failed spawn;
inspect current status and explicitly use a new instance ID for new work.

The distributed service adapter is tracked separately in the M3 ledger; SDK
support on PostgreSQL should not be confused with deployment-adapter qualification.

## Files, Git and integration — October 31 discussion

The dedicated [coding-agent presets and publishing design](CODING-AGENT-PRESETS.md)
records the three variants, recommended defaults, handoff and credential choices.

Sandbox placement and Git workspace layout are independent decisions. The current
default reuses the lead's original sandbox and working directory; builders and
verifiers immediately see those shared files. Local SDK sessions with sandbox off
also support explicitly selected child directories and Git worktrees with durable
preparation receipts. Remote workspace preparation and separate child-environment
provisioning are still implementation work. A worktree is file-edit coordination,
not a sandbox boundary: processes sharing a sandbox retain its aggregate access.

Spawning a child grants neither shell nor GitHub access automatically. A child
uses its assigned tools; an authorized shell tool can run Git within its execution
environment. Credentials and network grants remain application/provider policy.
Model credentials are not copied into the tool sandbox. No current team operation
automatically commits, pushes, opens a PR, or merges child changes into a repository.

Git integration is an optional coding-team workflow; research, support and other
teams do not need a repository. Do not make Git credentials or a checkout an
implicit requirement of generic team spawning.

Proposed coding workflow, for discussion before automatic Git integration:

1. Builders receive separate branches/worktrees rooted at an explicit base commit.
2. A verifier checks the exact submitted commit, rather than a moving working tree.
3. The lead reviews and integrates reviewed commits into its integration branch.
4. Push/PR publication is a distinct operation with the app's configured authority.

For separate sandboxes, workspace sharing cannot be assumed. Add an explicit
commit/patch-artifact handoff with base and result identifiers and verification
evidence. The receiving side must check the base and handle conflicts. This is
a proposed repository-integration layer; it is not implemented or implied by
team spawning. Transport uncertainty must preserve the original operation identity
and tell the model to inspect status before retrying non-idempotent Git/remote
operations. Never automatically repeat a push or merge after an unknown outcome.

### Service-side scoped types

A service team can opt into private definitions with `delegation` at creation.
Only tools present in the lead's activated release are eligible. Model choices
must be the release's default or one of its explicit working-model overrides.
The source profile must be activated and shared. No API key, server executable
or endpoint can be supplied by the lead model. Qualified tool selections resolve
to the original installed schema aliases; ambiguous aliases are rejected.

```typescript
const team = await session.createTeam({
  agentTypes: { builder: "builder@1", verifier: "verifier@1" },
  delegation: {
    tools: ["coding.read_file", "coding.write_file"],
    models: [{ provider: "primary", name: "working-model" }],
  },
});
```

This enables `team_define_type` for the lead. Its exact definition approval is
required before a type becomes usable; Ask, Auto-approve and Bypass retain the
same established meanings. The controller deterministically compiles the approved
revision into a private bundle and persists an immutable child template. Workers
reconstruct that same identity after restart using the original source release
and profile. The type is never an activated global release or a standalone agent
that another lead can start. Each spawn pins its approved type revision.

An optional per-child working-model choice resolves from saved deployment-owned
connection identities. It does not change the auto-approver. Missing configuration,
revoked source grants or a changed approved scope cannot trigger fallback. Known
preparation failure closes only the unstarted child and preserves its instance ID.

This slice supports only the original **team environment**. `new`/`member` sandbox
placement and nondefault remote directories/worktrees still reject explicitly.
They remain separate milestones, along with commit/patch exchange between isolated
sandboxes. The harness loop and sandbox provisioning remain outside ToolRunner.
