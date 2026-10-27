# Connect to OpenWorker Service — first milestone

This documents the M1 interface. Release/profile management and automatic
provisioning are now covered by the [M2 quickstart](SERVICE-M2-QUICKSTART.md).

The service runs the agent loop on workers and stores sessions, events and controls
in PostgreSQL. Your application connects over authenticated REST and SSE. Closing
an SDK connection does not cancel agent work.

This first implementation uses an **operator-configured agent** and an
**already-registered OpenShell environment**. Agent deployment, RuntimeProfile
creation, automatic environment provisioning, and `ows` are subsequent milestones.
The capability response advertises these limits explicitly. M1 uses one namespace
per service database; it is not multi-namespace tenant isolation.

## Operator setup

Use the existing `openworker-server-reference` binary and configuration, adding:

```json
"service": {
  "id": "development-cluster",
  "namespace": "development",
  "agent": "builder"
}
```

`agent` is the public name for the configured coworker bundle. The service ID and
namespace bind persistently to the PostgreSQL schema. Every API replica must use
that identity and compatible runtime configuration. A different binding fails at
startup. Keep this identity when restarting; do not reuse it for another deployment.

Run the existing migration and private administrator bootstrap before API startup:

```sh
openworker-server-reference --config service.json migrate
openworker-server-reference --config service.json auth-bootstrap
openworker-server-reference --config service.json api --node api-a --listen 127.0.0.1:8080
# In another terminal:
openworker-server-reference --config service.json worker --node worker-a --slots 2
```

The configuration supplies the database environment-variable name, coworker bundle,
Linux runner, model connection references, private bootstrap token file and OpenShell
mTLS configuration. See [server reference](../../harness/server-reference/README.md)
and its `config.example.json` for the full configuration. For initial local testing,
PostgreSQL can run in Docker and OpenShell can run on the existing local Kubernetes
cluster. The one-command Docker distribution is M3.

Register an OpenShell environment with the existing trusted-host operation:

```sh
openworker-server-reference --config service.json environment-create --request-id my-environment --shared
```

Retain the returned environment ID. Provisioning is an explicit operation: after an
unknown outcome, inspect/reconcile the original request; do not create a replacement.
Use scoped operator credentials with that environment grant in applications. The
bootstrap credential is for installation/administration, not end-user distribution.
Tokens remain outside source code. Remote service endpoints require HTTPS; plain
HTTP is accepted only on exact loopback hosts. Put TLS at the local ingress/proxy.

## TypeScript

Run this in the trusted server-side application process. Browser UIs use their
application's authenticated API, rather than receiving a service credential.

```typescript
import { Harness } from "@openworker/harness";

const service = await Harness.connect({
  url: "http://127.0.0.1:8080",
  namespace: "development",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
const builder = await service.agent("builder");
const session = await builder.newSession({
  environmentId,
  idempotencyKey: "create-project-42",
});
await saveSessionReference(session.reference()); // application persistence

const run = await session.send("Build a small game.", {
  idempotencyKey: "project-42-first-task",
});
for await (const item of run.events()) {
  sendToYourUI(item.event);
}
const result = await run.result(); // completed, interrupted, failed, or cancelled
service.disconnect(); // closes client connections only
```

`saveSessionReference`, `sendToYourUI`, and `environmentId` are application-owned.
A later process can reconnect and call `service.resume(savedReference)`, then
`session.resumeRun(submissionId)`. References pin service ID and namespace; they do
not contain credentials. `run.submissionId` identifies its durable submission receipt.
A `getToken(reason)` callback can replace `tokenEnv`; a rejected credential triggers
one refresh attempt. Supply only service credentials here, not model-provider keys.

## Rust

```rust,ignore
use openworker_sdk::{agents::Harness, service::ConnectOptions};
use futures_util::StreamExt;

let service = Harness::connect(ConnectOptions::new(
    "http://127.0.0.1:8080", "development", "OW_SERVICE_TOKEN",
)).await?;
let builder = service.agent("builder").await?;
let session = builder.new_session(&environment_id, Some("create-project-42")).await?;
let run = session.send("Build a small game.", Some("project-42-first-task")).await?;
let mut events = run.events(0);
while let Some(event) = events.next().await {
    let event = event?;
    // Forward the journal event to your UI.
}
let result = run.result().await?;
service.disconnect();
```

Rust local `Harness::connect(HarnessClient)` remains supported. Remote options return
service handles; they do not construct another agent loop in the client process.
Rust remote events currently expose journal events directly. TypeScript wraps them
in `RunEvent` with `summary: null`; use the journal's event type/data.

## Approvals and cancellation

An interrupted result includes durable interaction records. Answer using the
interaction's saved identity and policy binding, not reconstructed labels:

```typescript
const interaction = result.interruptions[0];
const receipt = await session.control(run.id, {
  kind: "response",
  interaction_id: interaction.id,
  response: "once", // approvals: once, session, deny; questions: user answer
  binding: {
    bundle_digest: interaction.bundle_digest,
    schema_digest: interaction.schema_digest,
    policy_digest: interaction.policy_digest,
    resource_policy_digest: interaction.resource_policy_digest,
  },
}, { idempotencyKey: "approve-project-42-write" });

// Or explicitly cancel:
await session.control(run.id, { kind: "cancel" }, {
  idempotencyKey: "cancel-project-42",
});
```

Control acceptance returns a receipt; it is not proof that the worker applied it.
Read `session.controlReceipt(receipt.command.request_id)` or follow session events.
`service.events(session.id, cursor)` follows the session across approvals and turns.
A run event iterator stops at a pending interaction or terminal run. A recoverable
worker transition is not terminal. Controls use separate HTTP requests while SSE
remains open.

## Delivery rules

- Reuse the same idempotency key and payload after a lost acknowledgement. A changed
  payload conflicts. Keys are scoped to principal, operation and namespace.
- The service generates session/run IDs. `x-request-id` is a separate tracing ID.
- M1 rejects a new turn while another is queued or active; exact retries still work.
- SDK mutations are never automatically replayed after an uncertain transport
  outcome. The error retains the idempotency key for explicit reconciliation.
- SSE reconnects with the last delivered cursor and suppresses duplicates. Explicit
  fallback URLs must pass the same service-identity and namespace handshake.
- Tool-side unknown outcomes remain governed by runtime recovery: reconnecting the
  UI or retrying admission is never permission to execute a tool again.
- Session snapshots and results enforce the existing PostgreSQL role/session grants.
  Legacy routes retain their existing contract. These are low-level M1 handles;
  typed high-level management and deployment APIs come next.

## Qualification

Build the TypeScript SDK first. Run the opt-in real-service test with a disposable
PostgreSQL database and an OpenShell 0.1.3 Kubernetes gateway:

```sh
npm --prefix harness/typescript-sdk run build
# Set OW_PG_TEST_URL, OW_PROBE_ENDPOINT, OW_PROBE_TLS_NAME, OW_PROBE_CERTS,
# OW_PROBE_IMAGE (digest-pinned), and OW_PROBE_RUNNER (matching Linux binary).
cargo test --manifest-path harness/Cargo.toml -p openworker-server-reference \
  --features openshell-test --test service_v1 -- --ignored --nocapture
```

The fixture starts two API processes and a worker, creates a dedicated sandbox,
checks both SDKs, then deletes its sandbox and schema. It uses a deterministic model
fixture and **real remote file tools**; it is not a live-model quality evaluation.
