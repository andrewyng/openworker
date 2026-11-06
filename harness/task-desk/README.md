# Task Desk

A small, single-operator application on the **existing** OpenWorker server API.
Submit tasks, stream progress, answer questions/approvals, cancel, and explicitly
save a completed assistant response as a durable downloadable Markdown artifact.
There is no second agent loop, database adapter, or tool execution in this app.

The dependency-free Node backend holds an **owned-scope operator** key with an
explicit environment grant. It refuses administrator/observer/all-scope keys.
The browser authenticates with a separate workspace access key and receives an
HttpOnly, SameSite cookie. Exact Host/Origin checks, route allowlisting, body/
connection bounds, inert artifact downloads and a restrictive CSP apply. Keys
are never browser configuration, URLs or command-line arguments. Trusted custom
API CAs can be supplied with `NODE_EXTRA_CA_CERTS`; verification is not disabled.

## Run

Node 20.19+ (or current supported LTS). No npm install/build step is needed.
Create a private JSON config outside version control:

```json
{
  "apis": ["https://harness.internal.example"],
  "api_token_file": "/private/task-desk-operator",
  "access_key_file": "/private/task-desk-access",
  "environment_id": "env_CONFIGURED",
  "coworker": "assistant-example",
  "workspace": "/sandbox",
  "port": 54350,
  "mode": "live"
}
```

```sh
node harness/task-desk/server.mjs /private/task-desk.json
```

Open `http://127.0.0.1:54350` and enter the access key. HTTP upstream APIs are
allowed only on explicit loopback IPs. Default app binding is loopback; keep it
there for a `kubectl port-forward pod/... 54350:54350` private deployment.
An explicit `private_network: true` permits all-interface binding behind a trusted
proxy. Such deployments must configure an exact `https://...` origin and TLS at
the proxy; this flag is not permission to publish an unauthenticated service.

For the existing dedicated local Helm fixture, resume its backing services and
harness, port-forward its API to localhost:9443, then:

```sh
node harness/task-desk/local.mjs
NODE_EXTRA_CA_CERTS=.ow-helm/private/api.ca.crt node harness/task-desk/server.mjs .ow-helm/task-desk/config.json
```

The setup tool creates only the app's owned-scope operator and separate browser
key. An exact setup retry returns the original credential, not a new identity.
This fixture uses a deterministic model, visibly labelled in the UI. Real MCP
tools still execute inside OpenShell. It is not live-model quality evidence.

### Qualified local live workspace

The 2026-10-11 experiment also qualified a separate live Sol principal on the
same dedicated local stack, without resetting earlier session/history data.
Its config is `.ow-helm/task-desk/live-config.json`; its browser access key is
`.ow-helm/task-desk/access`. Keep both private; do not paste keys into chat.
After resuming the existing backing services/harness, restart the API tunnel and
app in separate terminals:

```sh
kubectl --kubeconfig /absolute/path/to/test.kubeconfig --context YOUR_TEST_CONTEXT -n ow-helm-eval port-forward service/ow-openworker 9443:443 --address 127.0.0.1
NODE_EXTRA_CA_CERTS=.ow-helm/private/api.ca.crt node harness/task-desk/server.mjs .ow-helm/task-desk/live-config.json
```

These paths are this development machine's fixture, not portable installation
defaults. `local-live.mjs` is an explicit, scoped promotion helper: provider key
on stdin, model Secret only in workers, no automatic secret discovery. It has
already run for this workspace; normal restart does not require re-running it.
Do not regenerate Helm fixtures, which would replace saved live settings. Local
certificates expire after 30 days. The shared sandbox remains mutually trusted,
not per-user isolation. Live model calls incur provider charges when used.

Cloud deployment and measured results are documented in
[`deploy/aws/README.md`](../../deploy/aws/README.md).

## Reliability and tests

Browser submissions retain their original IDs/payloads across response loss or
reload. Unknown mutation outcomes are not automatically replayed with new IDs.
Controls carry the snapshot's exact four identity/policy digests. SSE reconnects
with its cursor; snapshots remain authoritative. Saving a response uses a stable
per-turn artifact request and SHA-256 over the actual assistant text. It does not
claim to publish workspace files or interpret a model-written path on the host.

```sh
node --test harness/task-desk/server.test.mjs
OW_PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node harness/task-desk/qualify.mjs
```

Browser qualification uses the real local TLS API/PostgreSQL/OpenShell/S3 stack:
submit, restore a pending approval after reload, Allow Once, completion, publish/
download/reload, cancel a waiting turn, and desktop/mobile layout checks. Evidence
is saved privately under `.ow-helm/task-desk/`. Seven backend tests cover scope,
CSRF/Host, route restrictions, exact session parameters, unknown outcomes,
artifact content and logout interrupting a stream.

## Deliberate limits

- One operator workspace, not user/tenant identity or SSO. All users with the
  access key share its principal. The example shared sandbox is for mutually
  trusted tasks only; it does not isolate sessions from each other's files.
- Browser cookies are in-memory, so app restart requires sign-in again; harness
  sessions, approvals and artifacts remain in PostgreSQL/S3. Run one app replica.
- Outstanding browser intents are local to that browser; no offline submission
  daemon. Don't clear browser storage to hide an uncertain outcome.
- API GET failover is allowed; mutations have no automatic network retry.
- Saved results are assistant responses, not automatically collected workspace
  files. Cancellation is not a promise to undo or kill already-started effects.
- No public hosting, live provider key, AWS deployment or production-readiness
  claim is implied by the local qualification.
