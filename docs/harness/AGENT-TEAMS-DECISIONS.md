# Agent teams: instances, inboxes and communication

Agreed October 30, 2026. This is the contract for the new first-class Teams API;
the existing SDK team-board API does not yet implement all of it.

**A team registers agent types and creates named instances.** `builder` is a type;
`builder-ui` and `builder-api` are independent instances, each with its own durable
session and inbox. Instance IDs are unique within a team and survive restarts.
Repeating the same spawn returns the original instance; changing its initial
request under that ID is a conflict. Follow-up work uses messaging. Team capacity
is configurable, supporting ten builders, ten verifiers and a lead.

```javascript
team.spawn({ agent_type: "builder", agent_id: "builder-ui", message: "Build the UI." });
team.send_message({ agent_id: "builder-ui", message: "Use the agreed API contract." });
team.wait_agent({ agent_ids: ["builder-ui", "builder-api"], timeout_ms: 30000 });
```

These are proposed model-facing tool shapes. The harness supplies registered agent
configuration and the team's execution environment; the model does not redefine
credentials, permissions or tool access through spawning.

**Every instance has a durable inbox.** Incoming messages are persisted with IDs,
source attribution and ordering. A busy agent receives a bounded batch before its
next LLM call, alongside the tool result. Tool-result identity and message
provenance remain distinct in provider wire formats. The delivery cursor advances
only when that input is durably recorded; overflow remains queued. An idle agent
wakes automatically. A waiting agent can wake for a message, a selected child's
completion or timeout. Explicitly paused/stopped agents retain messages until
resumed. Agent messages never grant human approval.

**The board is an ordered shared conversation. Only mentions notify.** Leads and
workers follow identical rules: `@builder-ui` targets that instance, multiple
mentions target each named instance once, and `@all` targets current members
excluding the sender. Recipients are resolved and persisted when posting; joining
later does not replay old broadcasts. A post without mentions is stored without
notification. Assignments, ownership and status changes cause no implicit
notifications. All members can read board history; mentions control delivery,
not visibility. Threads and external activity subscriptions are deferred.

History accepts a message-ID anchor, `before` or `after`, and a bounded count
(default 20, maximum 50). Anchors are exclusive; omitting one reads the latest
messages. Results are chronological and include continuation cursors. Mention
notifications include the original board message ID so recipients can fetch context.

**Communication is an application choice, enforced by the harness.** Default:
`communication: { board: true, directMessages: false }`. Applications can enable
either, both or neither for the entire team, equally for leads and workers.
Disabled tools are omitted and rejected on invocation. Direct messages are never
silently published to the board. Both paths are durable; direct messages go to
the recipient, while board history is shared. Disabling both still permits spawn's
initial task and results requested through `wait_agent`.

**Start with one shared OpenShell environment per team session.** Agents cooperate
on files and coordinate directories/ports. Each retains its own conversation and
configured tools. Waiting persists state and releases server execution capacity;
it does not delete the session, inbox or environment. Team budgets include child
work. Later, leads may request environments from application-approved RuntimeProfiles.
SQLite/PostgreSQL and in-process/subprocess/service deployments must share the
same coordination semantics; there is no second agent execution loop.
