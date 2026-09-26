# Approval guidance for coworkers and teams

Coworker authors can provide one or two paragraphs for the Auto-approve reviewer,
separately from the coworker's system prompt:

```yaml
approval_guidance: >-
  Independently verify the assigned acceptance criteria in the authorized local app.
  Project-local test dependencies and screenshots in assigned scratch are normal steps.
  Do not access production, publish changes, or send external messages without user scope.
```

This optional manifest field accepts up to 2,400 characters. It works for engineering,
security, content and other roles. Describe normal work and explicit boundaries, not
blanket instructions to approve everything. A lead's manifest also provides team-level
guidance when its workers are reviewed.

The lead can propose additional task-specific text in
`propose_team.members[].approval_guidance`. The staffing card keeps it under a collapsed
**Advanced** disclosure, editable for each worker. Creating the team saves the exact
human-returned text in the durable roster. Clearing it saves an empty paragraph. Omitting
it from the human decision does not grant the lead's suggestion. Later steering cannot
change this text; this version does not expose an agent-side update operation.

The reviewer receives the current coworker/team definitions, approved worker paragraph,
actual assignments and acceptance criteria, working folders, connectors, permission mode
and saved user rules. Delegated approvals resolve the original worker tool and arguments.
They do not treat the lead's recommendation as permission.

Direct owner messages are passed without semantic extraction or per-message truncation.
Source-tagged board wakes, connector deliveries and agent steering are not direct owner
messages. Unsourced worker inputs are separately labelled; historical agent steering is
not certified as human authorization. If the serialized request/history/context exceeds
64,000 characters, the result is uncertainty requiring a human, not silent truncation.
Changes to context invalidate pending review results.

Guidance informs a model judgment. It grants no connector or tool access, bypasses no
hard permission floor, and does not override user restrictions. Assignment text and lead
claims are not verified facts about credentials or disposable infrastructure. The reviewer
does not inspect referenced script bodies or environment files, so an opaque script may
still require human confirmation.
