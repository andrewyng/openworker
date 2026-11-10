# API authorization

Authorization answers **who may use which session**. It is independent of the
agent's tool approvals, allowed files/network, credentials and sandbox policy.
An administrator API key is not a tool-policy bypass.

```text
App key → API node → PostgreSQL credential + session check
                             │
                   command + identity + audit commit
                             │
                      worker → tool policy → sandbox
```

## Roles and scope

| Role | Authority |
|---|---|
| observer | Read authorized sessions, receipts, progress, artifacts and API health. |
| operator | Also create sessions in explicitly granted environments, submit turns, answer/approve/cancel and publish artifacts in scope. |
| administrator | All sessions; issue/list/revoke credentials and read security audit. |

Every non-admin key also has immutable scope: `all`, `owned`, or
`sessions` with exact existing IDs. `owned` means sessions created through the
API by that **principal**, not just by that particular key. Rotation can retain
ownership by using the same principal. An exact-session key cannot create new
sessions. Operators may create only in administrator-granted `environments`;
an empty list grants no creation. Session listing filters before pagination.
Old/trusted-worker-created sessions have no inferred owner: use explicit IDs
or `all` to grant access. Role/scopes must be changed by issuing a new key.

The local reference bootstraps a trusted administrative key for its test CLI
and a separate observer for the console. Partner applications should receive an
operator scoped to their principal/sessions/environments, not that admin key.

## Provisioning and rotation

The deployment owner explicitly runs `migrate`, then `auth-bootstrap`, using the
private `api_token_file` and `actor` in reference configuration. Bootstrap only
creates the first administrator in an empty registry. It does not reset an
existing registry, silently accept a new file token, or revive a revoked key.
There is no HTTP bootstrap endpoint. API authentication uses PostgreSQL, not a
master-key file fallback. Database/OS administrators remain trusted.

Generate each bearer from **at least 32 random bytes** with a cryptographic RNG;
encode hex or URL-safe characters (32–256 characters accepted). Store it in a
private file or secret manager. Syntax validation cannot detect a weak password.
Never put keys in URLs, command arguments, logs, issues or source control.
Only a domain-separated SHA-256 digest is stored in PostgreSQL; metadata and
audit endpoints return neither bearer nor digest. Losing the bearer requires
issuing a new key. V1 has explicit revocation, not automatic expiration.

Example private `operator.json` (replace the granted environment ID):

```json
{
  "principal": "example-app",
  "role": "operator",
  "scope": { "kind": "owned" },
  "environments": ["env_existing_environment_id"]
}
```

From `harness/`, with a valid administrator key in configured `api_token_file`:

```sh
target/debug/openworker-server-reference --config /private/reference.json auth-bootstrap
target/debug/openworker-server-reference --config /private/reference.json auth-issue --spec /private/operator.json --token-file /private/operator-token --request-id example-key-001
target/debug/openworker-server-reference --config /private/reference.json auth-list
target/debug/openworker-server-reference --config /private/reference.json auth-revoke --credential-id key_to_revoke
```

Issue is idempotent only for the same request ID, principal issuing it, token and
spec. Replaying a revoked issuance returns the revoked record, never a new active
key. Clients must inspect `revoked`. Rotate by issuing a distinct token/request
ID, switching clients, and revoking the old key. Revoking the last active admin
is rejected; provision/test another administrator before rotating that key.
Configuration is still managed by the deployment owner, not a remote shell or
arbitrary configuration-edit API.

## HTTP additions

All endpoints require `Authorization: Bearer …`, including health. All unmatched
route/method combinations are denied. JSON endpoints reject unknown fields.

| Method/path | Request / response |
|---|---|
| `GET /auth/me` | Any active key → resolved principal, credential ID, role, scope, environments. |
| `POST /credentials` | Admin; `{request_id, spec, token}` → `{id, request_id, spec, revoked}`. |
| `GET /credentials?after=KEY_ID` | Admin; `{credentials, next_after}`, up to 100 records. |
| `POST /credentials/KEY_ID/revoke` | Admin; idempotent, returns revoked metadata. |
| `GET /security/audit?after=0` | Admin; up to 100 events plus `next_after`. |

Missing/invalid/revoked bearer → 401; insufficient authority/out-of-scope ID →
403; malformed spec → 400; conflicting issuance/last-admin revoke → 409;
unconfigured/unavailable storage → service error. No authentication fallback
when PostgreSQL is unavailable. Use TLS outside the loopback-only reference;
credential issuance bodies are secrets and must be excluded from proxy logs.

## Revocation, audit and streaming

Each API request checks the shared registry. Mutations recheck and lock the key
in the **same transaction** that admits the command and writes its security audit.
Revocation waits for previously admitted transactions, then prevents new ones
on every node. Already accepted commands or started tool effects are **not
cancelled or rolled back** by revoking a key; use an authorized cancellation.

SSE rechecks every event read (250ms polling); failure closes the stream with a
generic reconciliation error. Downloads recheck before each object chunk;
uploads recheck incoming chunks and the final ready-publication transaction.
Previously authorized/buffered bytes may finish; they cannot be retracted.
An interrupted upload can leave pending/orphan objects. Revocation does not
grant permission to resume or reconcile those objects.

Durable audit records principal/key, operation, session/request/target IDs and
outcome, not prompts, results, bearer values or hashes. Mutation audit failure
rolls back admission. `admitted` is **not** proof the external effect succeeded;
some exact retries also create admission records. Identified authorization
denials are best-effort audit; reads/unknown-key attempts are not a comprehensive
access log. Add ingress rate limits, security monitoring and an audit retention
policy for production. Audit appends serialize through commit to keep cursors
safe across concurrent transactions; benchmark this shared append point at scale.
The registry currently caps issuance at 1,024 records,
including revoked tombstones; no automatic pruning/resurrection.

## Boundary and remaining work

The reference API is loopback-only and rejects browser Origin requests. The
console verifies an observer key at startup, keeps it server-side, and has its
own distinct browser login key. Its proxy allowlist is additional defense, not
the authority boundary: direct observer API mutations are also denied.

This is not physical tenant isolation. Intentionally shared sandboxes have
shared filesystem/network authority even when session metadata is scoped.
Use separate environments/credentials for mutually untrusted principals.
Health includes service-level telemetry counters, not session content.
Unscoped worker/store calls and in-process SDK hosts with DB/OS access remain
trusted; `authorized()` guards the documented API operations, not every generic
store/SDK method against hostile Rust code. No Desktop/stdio/local SDK change.

SSO, user-directory integration, expiry, fine-grained approval-only roles,
per-coworker/model/resource admission ceilings, tenant quotas, distributed rate
limits and production TLS/IAM remain integration work. In particular an operator
can choose existing session-creation permission modes; API roles do not define
an additional organization-wide tool-policy ceiling.

Design guidance: [OWASP authorization recommendations](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).
