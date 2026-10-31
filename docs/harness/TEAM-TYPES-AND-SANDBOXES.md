# Agent types, team management and sandbox placement

Decision record: 31 October 2026. Accepted in discussion with Rohit.
Implementation tracking: [plan and ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md).
Baseline: `f13de9b9`, public branch `openworker-harness-sdk`.

This document separates the team implementation we are extending now from a
future integration in which the OpenWorker agent loop itself runs inside an
OpenShell sandbox. The future integration must not block the current work.

## What exists today

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
sandboxed coding operations. There are no model-defined session-local types,
per-member sandbox choices or individual retirement tools yet. Application-driven
`agent/register` is harness-wide and must not be exposed directly as the new
model-facing definition tool. Legacy `.subagent()`/work-item teams and Python's
staffing proposals retain their separate contracts.

Sources: runtime `agent_team.rs` and `team_coordination.rs`, SQLite
`sqlite_agent_team.rs`, PostgreSQL `agent_team.rs`, SDK `host.rs`/`host_runs.rs`,
and service `agent_teams.rs`/`management.rs`.

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
