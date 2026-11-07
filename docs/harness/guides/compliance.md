# Compliance and SIEM

Give authorized investigators a record of agent activity: which session acted,
which policy/check applied, whether an action was allowed or blocked, and what
committed outcome was observed. Compliance evidence is distinct from performance
traces and from the model's own explanation of its work.

## Capture and inspect

Compliance capture is opt-in. SQLite and PostgreSQL record metadata with the
execution event transaction. Required evidence failures stop protected work rather
than silently producing an unaudited operation.

- A compliance reader can query scoped metadata.
- A separate compliance-auditor permission can access accepted conversation content.
- The management UI's Safety view shows findings without exposing rejected payloads.
- Rejected model candidates stay out of accepted history and public streams;
  metadata records the check, version, outcome and reason.

## Export and retain

The optional SIEM exporter delivers metadata batches with stable event IDs and
durable checkpoints. Delivery is at least once: a receiver should deduplicate IDs
and acknowledge only after accepting the batch. Retrying an export never retries
the agent's tool action.

Set retention and pending-event limits. Backpressure can stop new work while
allowing completion/cancellation evidence and export to drain. Query expiry and
physical pruning are separate; schedule the trusted maintenance API when you need
expired metadata physically removed. Pending exports remain protected from pruning.

## External security checks

Hooks can send selected context to an external scanner before a protected step.
The Prisma AIRS adapter is experimental; validate it against your tenant and
profile before relying on it. Do not equate an external scanner with an audit log or implicitly
upload the whole conversation.

See [guardrails](guardrails.md) and [observability](observability.md).
This provides auditability, not a regulatory certification or tamper-proof storage
against a database administrator. Legal holds and immutable archives are separate work.
