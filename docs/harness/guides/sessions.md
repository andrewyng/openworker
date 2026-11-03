# Sessions and teams

An agent is a reusable definition. A session is its durable conversation and
execution state. Sending a message starts a run; the run exposes events and a final
result or interruption. Keep the session reference in your application database.

Use `session.state()` for current activity, pending interactions and outstanding
timers. On restart, reattach to the saved session rather than creating a replacement.
Reconnect/replay does not mean replaying side effects. If an action's outcome is
unknown, inspect its status before retrying it.

## Approvals and questions

Render durable approval/question interruptions in your UI, then send the user's
actual response. “Allow always” applies to the session. Auto-approval uses a
reviewer; an inconclusive decision still asks the human. Bypass skips human approval
but does not remove required guardrails or sandbox restrictions.

A tool-free guide can disable optional tools, memory, planning and scheduling.
Auto-skip questions is a separate setting, not an authorization mode.

## Teams

A lead can create multiple instances of registered agent types, communicate through
durable inboxes/board mentions and wait without keeping an execution slot occupied.
Shared workspaces use separate worktrees; independent sandboxes exchange changes
through explicit handoff. Team creation does not enlarge the lead's permissions.

See [agent APIs](../AGENTS-API.md), [team implementation](../AGENT-TEAMS-IMPLEMENTATION.md),
[session grants](../SESSION-GRANTS.md) and [storage](../SDK-STORAGE.md).
