# Two API / two worker reference

For the opt-in versioned service API and Rust/TypeScript `Harness.connect()`, see
[Service M1 quickstart](../../docs/harness/SERVICE-M1-QUICKSTART.md).
For release deployment, RuntimeProfiles and automatic OpenShell provisioning, see
[Service M2 quickstart](../../docs/harness/SERVICE-M2-QUICKSTART.md).

A deliberately small native Rust server adapter and Node client, using the same
runtime as Desktop/stdio/SDK. No second agent loop and no Python dependency.
This demonstrates the production boundary, not a complete multi-tenant product.

```text
Client ── REST + SSE ── API A / API B
                              │
                       PostgreSQL v5
                              │
                       Worker A / Worker B
                              │ verified mTLS
                       OpenShell gateway
                              │
                    original sandbox + PVC
```

APIs commit commands and stream durable events. They do not forward a live
worker socket or route users to a particular worker. Workers independently claim
compatible sessions, renew leases and prioritize approvals/cancellation. An API
may disappear without losing a session, pending decision or event cursor.
Polling PostgreSQL every 250ms is an implementation detail between the API and
store; clients receive SSE. Notifications can optimize this later, not replace
durable replay. API, worker and tool execution capacity are separate.

## Prerequisites and configuration

- Rust 1.96, Node 20+, PostgreSQL, and a qualified OpenShell **0.1.3 Kubernetes**
  gateway with persistent workspace storage and verified mTLS. See
  `../../deploy/local/README.md` for the isolated local cluster profile.
- A pinned Linux runner image containing `/opt/openworker/openworker`. The
  `runner` configuration is the corresponding Linux ELF identity input, not a
  host command to execute. Keep replicas within each execution cohort on the same
  verified build/bundle. Retain old cohorts during upgrades; see
  [version-aware pools and rollback](../../deploy/reference/UPGRADES.md).
- Copy `config.example.json` outside the repo and replace paths/digest. The
  placeholder image is intentionally invalid. `state_root` is controller-local;
  it is **not** the remote agent workspace. Give each process a distinct node ID.
- Put a random 32–256-character URL-safe bearer token in a private file. Do not
  put tokens or database passwords in command-line flags, source or screenshots.
  Supply the PostgreSQL URL via the named environment variable. Verified database
  TLS is default; `database_loopback_plaintext: true` is evaluation-only and rejects
  non-loopback endpoints. Never point qualification tests at application databases.

The sample uses `scripted: true` for deterministic protocol qualification. This
still executes real MCP file/shell tools inside the sandbox. For live-model use,
set `scripted: false` and an available model ID; supply `OPENAI_API_KEY` only to
workers using a deployment secret. Do not send it to tools, the sandbox or this
HTTP API. This reference does not expose model-connection management or configure
an auto-approver; the underlying runtime APIs remain available to a richer host.

## Run locally

From `harness/`, build `cargo build --locked -p openworker-server-reference`.
The following commands use `target/debug/openworker-server-reference`; substitute
your build output directory if configured differently. Use a **new development
schema**: v5 rejects v1–v4, without migration/reset.

```sh
target/debug/openworker-server-reference --config /private/reference.json migrate
target/debug/openworker-server-reference --config /private/reference.json auth-bootstrap
target/debug/openworker-server-reference --config /private/reference.json environment-create --request-id setup-001 --shared
```

Save the returned environment `id`. Reusing that request ID reads the same
receipt; it never silently recreates an unknown environment. Provisioning timeout
requires inspection of the original object, not a fresh request ID. `--shared`
means all attached sessions share filesystem/network authority; omit it for
exclusive use. Do not use shared environments across mutually untrusted tenants.

Start these four commands in separate terminals/processes:

```sh
target/debug/openworker-server-reference --config /private/reference.json api --node api-a --listen 127.0.0.1:8081
target/debug/openworker-server-reference --config /private/reference.json api --node api-b --listen 127.0.0.1:8082
target/debug/openworker-server-reference --config /private/reference.json worker --node worker-a --slots 2
target/debug/openworker-server-reference --config /private/reference.json worker --node worker-b --slots 2
```

The API is loopback-only, role/session-authorized, and rejects browser `Origin`
requests. All endpoints require an active PostgreSQL-registered key, including health.
`/healthz` is liveness; `/readyz` checks this API's PostgreSQL connection. A
poisoned connection remains unavailable until its API process is restarted.
See [authorization](AUTHORIZATION.md) for observer/operator/admin keys, session
and environment grants, rotation/revocation and audit. This is not physical tenant
isolation. A production app supplies TLS ingress, quotas and admission policy. Do not expose this
example directly to an untrusted network. Worker exit is fail-stop; supervise
restarts and alert on failures rather than hiding them with mutation retries.

Planned worker termination now drains current execution before interruption;
first SIGTERM/Ctrl-C drains, second interrupts. Defaults are 300s drain plus 45s
cleanup. See [the drain contract](../../deploy/reference/DRAINING.md) for flags,
supervisor budgets, pending approvals and the version-pinned session boundary.

```sh
export OW_API_TOKEN_FILE=/private/api-token
export OW_API_URLS=http://127.0.0.1:8081,http://127.0.0.1:8082
node server-reference/client.mjs --environment ENV_ID --request-id demo-001 --fixture
```

Choose Allow Once, Allow for this Session, or Deny at the prompt. For a live
worker use `--text 'Create a short packing checklist in checklist.txt'` instead
of `--fixture`. To reconnect without submitting another task:
`node server-reference/client.mjs --session SESSION_ID`.
The client prints request/session IDs and reconnects with its last cursor. Reuse
the printed `--request-id` after response loss; a new ID means a new task.

## Reconcile uncertain environment provisioning

These are trusted host/database administration commands, not browser endpoints.
After a provisioning timeout, retrieve the original receipt with the same
`environment-create --request-id` (no new object is created on that exact retry),
then inspect it and explicitly reconcile the original remote name:

```sh
target/debug/openworker-server-reference --config /private/reference.json environment-inspect --environment-id ENV_ID
target/debug/openworker-server-reference --config /private/reference.json environment-reconcile --environment-id ENV_ID --remote-name ORIGINAL_SANDBOX_NAME
```

Reconciliation only reads the remote object and records a verified binding. It
requires the original ownership label, matching provider/image/policy profile,
and ready/admitted policy. It never creates, replaces, starts or deletes a sandbox.
Missing/changed/not-ready objects fail explicitly. A cloud cold start may exceed
the 120-second initial wait; a timeout is not proof of failure or permission to
create a second object. Keep the original receipt and inspect the original state.

Managed PostgreSQL CA files may be multi-root PEM bundles; all certificates are
validated and loaded (at most 256 KiB / 32 roots). Empty/malformed/non-certificate
PEM material is rejected. TLS and hostname verification are never disabled.

## HTTP contract (reference v0)

For restore inspection, set `recovery_read_only: true`: authenticated reads and
artifact downloads work without gateway access; HTTP mutations and worker/
environment execution commands refuse. Default false; `/readyz` identifies the
mode. See [backup and recovery](../../deploy/recovery/README.md) for the local
restore procedure and storage-identity, credential-rollback and effect limits.

For a repeatable local stack and optional read-only console/Grafana dashboards,
see [the deployment reference](../../deploy/reference/README.md).

Optional durable artifacts: the [storage and API guide](../artifacts/README.md)
describes filesystem/S3 configuration, explicit upload/download/reconciliation,
and `artifact-client.mjs`. Artifacts stay retrievable when their worker exits;
workspace uploads are never automatic. Existing session APIs are unchanged.

Optional, off-by-default OpenTelemetry: add `"telemetry":{"endpoint":
"http://127.0.0.1:4318"}` to the configuration. The customer-selected collector
receives content-free traces, metrics and lifecycle logs. Execution never waits
on collector I/O. See [telemetry setup, bounds and limitations](../telemetry/README.md).

| Request | Purpose |
|---|---|
| `POST /sessions` | `{request_id, parameters}`; same actor/payload returns same session; changed payload is 409. Parameters use the existing runtime shape plus `environment_id`. |
| `GET /sessions` | Bounded operational directory, `limit` 1–100 (default 50), optional `after` session ID; newest creation first. No history/prompt payloads. |
| `GET /sessions/:id` | Coherent snapshot and event cursor, pending interactions, bounded messages/attempts. |
| `POST /sessions/:id/turns` | `{submission_id, text}`; returns a durable receipt with stable `turn_id`, initially queued. |
| `GET /sessions/:id/turns/:submission_id` | Inspect admission/completion after response loss. |
| `POST /sessions/:id/controls` | `{request_id, turn_id, action}`; separate priority channel, not behind a waiting turn. |
| `GET /sessions/:id/controls/:request_id` | `pending`, `applied` or `rejected`; pending is not applied permission. |
| `GET /sessions/:id/events` | SSE `progress` frames; `Last-Event-ID` (preferred) or `?after=N` supplies cursor. Existing event JSON uses `type`, not `kind`. |

Approval/question `action` is `{kind:"response", interaction_id, response,
binding:{bundle_digest,schema_digest,policy_digest,resource_policy_digest}}`.
Copy identities from the pending snapshot. Approval response is `once`, `session`
or `deny`; a question uses an offered answer. The API stamps its configured
principal, not a client-supplied actor. Cancellation is `{kind:"cancel"}` and
targets the current turn. It cannot undo an already-admitted external effect.

Control errors are explicit: malformed request 400, missing receipt 404,
changed idempotency payload or target conflict 409, closed session 409, and full
queue 429. New controls on closed sessions reject; exact retries still retrieve
the original receipt. Trusted runtime/store close must wait for already-admitted
controls to settle (`controls_pending`) rather than stranding them. Unknown
storage failures return 503: reconcile the original durable ID before retrying.

SSE IDs are contiguous per session, not globally ordered. On `stream_error`,
reconnect/reconcile using a snapshot and the last received ID. A snapshot's cursor
and state come from the same transaction; follow events strictly after that
cursor. No push is required for correctness. Only the cursor is a replay marker;
queue position, turn ID, owner epoch and canonical revision are different values.

## Qualification and limits

The opt-in test `cargo test --locked -p openworker-server-reference --features
openshell-test --test replicas -- --ignored --nocapture` needs the same
`OW_PG_TEST_URL` and `OW_PROBE_*` variables as the store's real OpenShell test.
It launches two actual APIs and two worker processes, exercises racing creation,
approval identity after replacing APIs/workers, cursor replay on the other API,
real file effects and concurrent sandbox tool calls with cross-API cancellation.
It deletes its successful disposable environment; failures print exact IDs for
explicit cleanup. No live model credentials are used by the test.
Set `OW_PROBE_NODE=node` to additionally run the shipped client against the real
API/worker/tool (the selected test session uses explicit bypass to avoid an
interactive prompt in automation).

This is multiple independent processes on a local host plus a real Kubernetes
execution environment—not multi-AZ, physical-node or cloud failover evidence.
Kubernetes scheduling/PVC recovery follows the provider's guarantees. OpenShell
disconnect may leave effects running, and its delete API has no expected-UID
precondition; prohibit out-of-band delete/recreate races during managed lifecycle.

Still outside this example: full REST coverage (session close/history pages/model
connections/grant revocation), shared credential service, tenant authz, gateway
provisioning automation, artifact/object storage, full OTel, HA database operations,
TLS ingress and AWS/Azure deployment automation. Manage final session/environment
closure through the trusted runtime/store APIs; do not delete shared environments
as a side effect of client disconnect. Desktop and the local SDK keep their
existing transports and default SQLite behavior.
