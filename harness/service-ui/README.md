# OpenWorker management UI

A separate browser UI for OpenWorker Service. The UI server calls the same authenticated
REST API as `ows`; it has no PostgreSQL, Kubernetes, model, or OpenShell credentials.

## Start locally

Requires Node.js 20+ and a running OpenWorker Service with the M5 listing endpoints.
There is no package install or frontend build step. Rebuild/redeploy the service
from the same revision first if your existing image predates the listing endpoints;
see [service build instructions](../../deploy/service/README.md#build).

Save a configuration file (these are connection details, not credentials):

```json
{
  "serviceUrl": "https://your-service.example.com",
  "serviceId": "your-service-id",
  "namespace": "your-namespace",
  "port": 58641
}
```

Use the service ID/namespace from your deployment configuration. For a local service,
`http://127.0.0.1:PORT` is accepted. For a private CA, start Node with
`NODE_EXTRA_CA_CERTS=/path/to/ca.crt`; do not disable TLS verification.

```sh
node harness/service-ui/server.mjs /path/to/ui.json
```

Open http://127.0.0.1:58641 and sign in with an existing service token. Issue scoped
credentials with `ows credentials issue` as described in the
[M4 quickstart](../../docs/harness/SERVICE-M4-QUICKSTART.md). There is no public bootstrap
or default password. Use separate runner/publisher/administrator credentials as needed.

## What is connected

- **Overview / Activity:** real, scope-filtered session summaries and session counts
  by day. Charts describe only the loaded page; they are not global usage totals.
- **Agents:** visible immutable releases, definition inspection, publication,
  activation, and task submission. Release JSON selects tools already installed in
  the service. It cannot upload native binaries or arbitrary server launchers.
- **Sessions:** paginated directory, filters, recorded messages/results, expandable
  event trace, SSE updates, sending turns, approvals/questions, and cancellation.
  Decisions carry the exact run/interaction and policy bindings shown to the user.
  Durable control receipts distinguish requested decisions from applied decisions
  and remain visible after a browser reload.
- **Infrastructure:** real desired/observed worker capacity, revision-safe manual
  scaling, RuntimeProfile creation/activation, and administrator environment inspection.
- **Settings:** revision-safe admission/compaction/limit configuration, expiring
  service-token issue/revoke, and bounded security audit inspection.

Controls are shown according to the signed-in role; the service reauthorizes every
request and filters resource scope. Service administration does not grant tool
approval or run authority. Publishing and activation remain separate capabilities.

Grafana/Langfuse are reserved UI locations, not newly implemented exporters. Provider
credentials and endpoint enrollment remain deployment-managed. The UI is not an SSO
implementation, a cloud-node autoscaler, or a replacement for OpenShell provisioning.

## Browser and process boundary

Tokens are held only in UI-server memory. The browser receives an opaque HTTP-only,
SameSite=Strict cookie and a CSRF token; service tokens are not saved in browser
storage, URLs, configuration files, or logs. Login fields are cleared immediately.
An explicitly issued new token is shown in its drawer for copying before issuance;
it is cleared on close. Private browser mode is useful on shared machines.

Sessions expire after 30 minutes (configurable `sessionSeconds`, capped at one hour),
or earlier when the service credential expires. Service revocation is checked on
upstream requests and streams. Logout/expiry abort open upstream requests. Restarting
the UI requires signing in again and does not cancel harness work.

The fixed upstream and service/namespace headers cannot be changed by the browser.
Routes and query parameters are allowlisted. Host/Origin/Fetch-Metadata checks,
CSRF checks on writes, response size limits, bounded sessions/concurrency, and a
restrictive CSP protect the browser boundary. The service still rejects direct
browser Origin requests. No generic forward proxy is exposed.

Writes are never retried automatically. A lost response retains the exact body and
idempotency key for **Retry same request**. Do not refresh the page while resolving an
uncertain write; copy the displayed idempotency key when investigating it. A changed
configuration or scale revision must be re-read before a new update. No UI retry
replays a tool action whose result is unknown.

## Container

```sh
docker build -t openworker-service-ui:local harness/service-ui
```

For a container, add `"host":"0.0.0.0"`, `"port":8080`, and an explicit
`publicOrigin` to `ui.json`. Bind the published port to loopback for local testing:

```sh
docker run --rm --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  -p 127.0.0.1:58641:8080 \
  -v /absolute/config-directory:/config:ro \
  openworker-service-ui:local
```

The file inside the mount is `/config/ui.json`. For this local example set
`publicOrigin` to `http://127.0.0.1:58641`. The container must be able to reach the
configured service URL. `allowInternalHttp: true` is an explicit local-test option
for e.g. `http://host.docker.internal:PORT`; production uses authenticated HTTPS to
the service. Never mount an API administrator token into the UI container.

## Kubernetes

The existing service chart has an optional `managementUI` Deployment and Service:

```yaml
managementUI:
  enabled: true
  image: registry.example.com/openworker-ui@sha256:YOUR_VERIFIED_DIGEST
  publicOrigin: https://manage.example.com
  serviceUrl: https://ows-api.your-namespace.svc
  caSecret: service-api-ca # optional; ca.crt must trust the API certificate
```

The API certificate must match `serviceUrl`. Route your TLS ingress to the chart's
`<release>-ui` Service, preserving the original Host and Origin headers and disabling
SSE buffering. Restrict direct access to the UI pod to your trusted ingress. The UI
uses secure `__Host-` cookies when `publicOrigin` is HTTPS; forwarded headers cannot
override that configured origin. The chart requires HTTPS origins and digest-pinned
images. No ServiceAccount token or execution/database Secret is mounted.

This version runs one UI replica with in-memory browser sessions and Recreate
rollouts. APIs/workers can remain replicated independently. A UI rollout signs users
out; durable harness work survives. Shared browser session storage or sticky-routing
support is needed before scaling UI replicas. SSO remains a later addition.

## Verification

```sh
node --test harness/service-ui/test/server.test.mjs
node --test deploy/service/chart.test.mjs
```

Real PostgreSQL/API/browser acceptance (use a disposable PostgreSQL instance):

```sh
export OW_PG_TEST_URL='postgres://...'
export OW_PROBE_RUNNER=/absolute/path/to/linux/openworker
export OW_PLAYWRIGHT_MODULE=/absolute/path/to/playwright-or-playwright-test
cargo test --manifest-path harness/Cargo.toml -p openworker-server-reference \
  --test management_ui -- --ignored --nocapture
```

Add `OW_UI_CONTAINER_IMAGE=openworker-service-ui:local` to run the same suite against
the read-only Linux UI container on Docker Desktop (uses port 58643 and
`host.docker.internal`). The test creates and removes its own database schema/UI
container. Fixtures exercise real scoped reads, durable control admission, config,
capacity requests, release/profile publication and credential lifecycle. They do
not execute a model or apply tools; those worker/provider tests remain separate.

## Design mock

The original sample-data mock is preserved for visual comparisons:

```sh
node harness/service-ui/preview.mjs
```

It opens on port 58640. The production UI lives in `public/`; the mock lives in
`mock/`. Sample records and chart values are never used as a live-data fallback.
