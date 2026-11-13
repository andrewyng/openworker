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

## Configure capture and delivery

```typescript
const page = await session.complianceActivity({ limit: 100 });
const next = await session.complianceActivity({ after: page.nextAfter, limit: 100 });
const health = await session.complianceDeliveryHealth();
```

The service provides:

- `GET /v1/compliance/sessions/{id}/activities?after=0&limit=100`
- `GET /v1/compliance/sessions/{id}/delivery`
- `GET /v1/compliance/sessions/{id}/content?after=0` with separate content authority.

A `compliance_reader` can read metadata only; a `compliance_auditor` can also read
accepted conversation content. Both retain their session scope. General observer
access does not grant compliance authority. Successful compliance reads are
recorded in the security audit. The management UI's administrator Safety tab uses
metadata only; the browser proxy does not expose the content endpoint.

Enable the optional exporter in the host binary, then configure a sink:

```typescript
const harness = await Harness.start({
  compliance: { retentionDays: 30, maxPendingEvents: 10000 },
  complianceSinks: [{
    name: "security-events-v1",
    endpoint: "https://siem.example.com/openworker/events",
    credentialEnv: "SIEM_TOKEN",
  }],
});
```

Only the named credential is inherited by the trusted child. The endpoint receives
JSON batches of metadata activities with stable per-event IDs. The receiver should return 2xx only after durably accepting the batch. HTTP success
acknowledges a batch; timeout/connection loss retries delivery with those same
IDs. Changing a destination requires a new sink version. There are no redirects
and no automatic tool retries. Restore a failed receiver before its pending
backlog grows too large. The configured threshold blocks new turn/model/tool
admission while allowing completion/cancellation evidence and export to drain.

Metadata retention is independent of canonical conversation storage. Expired
metadata is omitted from query pages; trusted maintenance can prune it. Registered
unacknowledged export records are retained until delivered. This is not a legal
hold, immutable archive or protection against a privileged database administrator.
