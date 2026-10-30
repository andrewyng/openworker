# OpenWorker management UI

A separate browser UI for OpenWorker Service. The UI server calls the same authenticated
REST API as `ows`; it has no PostgreSQL, Kubernetes, model, or OpenShell credentials.

## Start locally

Requires Node.js 20+ and a running OpenWorker Service from the same revision,
including its listing and observability endpoints.
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
- **Observability:** authorized run search, selected-page usage summaries,
  model/purpose and tool groups, and watermarked turn timelines. Missing usage
  stays unknown; scan/group limits remain visible. See below for counting and
  process-health scope.
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
node --test harness/service-ui/test/server.test.mjs harness/service-ui/test/observability.test.mjs
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

## Complete-stack local Kubernetes qualification

`deploy/service/qualify-ui-kubernetes.mjs` drives a real browser against a UI pod,
Rust API, PostgreSQL, service worker and NVIDIA OpenShell sandbox. It uses the
scripted test model to exercise real tool effects without a model API key.

Use the dedicated kind installer from the M4 guide with a freshly built service
image containing the UI listing endpoints, the management UI image, and the matching
runner image imported by digest. Before installing the service, extend its local
certificate to the API Service DNS name:

```sh
node deploy/service/kubernetes-local.mjs init .ow-service-ui /private/install.json
node deploy/service/ui-kubernetes-local.mjs prepare .ow-service-ui
node deploy/service/kubernetes-local.mjs install .ow-service-ui
node deploy/service/ui-kubernetes-local.mjs install .ow-service-ui UI_IMAGE_DIGEST
OW_PLAYWRIGHT_MODULE=/absolute/path/to/playwright-or-playwright-test \
  node deploy/service/qualify-ui-kubernetes.mjs .ow-service-ui
```

The helper installs a local TLS ingress and uses loopback port-forwards on 58444
(API) and 58644 (UI). Node verifies the generated CA; the test browser trusts only
the generated leaf certificate's SPKI. Production TLS validation is unchanged.
Namespaces must satisfy the existing dedicated kind installer's naming/ownership
checks. There is no Red Hat OpenShift installation in this qualification.

The test publishes a profile/release through the UI, submits a task, approves each
required tool call, restarts the API while approval is parked, restarts the UI during
execution, and independently reads a persisted file to verify exactly one append.
It also cancels an active command, scales to two ready workers, closes the session,
and deletes its remote environment. A remote cancellation can correctly finish as
`interrupted` with `outcome_unknown`; the test requires an applied cancel receipt,
no tool replay, and durable unknown-outcome history rather than claiming rollback.

A failed run retains a journal and never starts the original task again automatically.
Inspect its durable state first. The two explicit resume switches in the script
only support their recorded, known checkpoints. After successful session/environment
cleanup, use the existing guarded destroy command with the exact deployment ID:

```sh
node deploy/service/kubernetes-local.mjs destroy .ow-service-ui EXACT_DEPLOYMENT_ID
```

Then dispose of copied private input/token files. Keep only the sanitized report and
screenshots needed for review. This fixture is local ARM64 kind/OpenShell qualification;
AWS/GCP, x86-64, and Red Hat OpenShift require their own deployment evidence.


## Observability

Open **Observability** for summaries of a recent page of up to 25 authorized runs.
A run is one submitted turn, not an entire session. Page navigation replaces the
selection; these charts are not unbounded deployment totals. Activity and Overview
retain their existing operational session charts.

**Find a run** queries the service as you type (250 ms debounce). Results are
paginated, with at most 100 matches retained in the picker; narrow the search for
more. Arrow keys and Enter choose a result; Escape closes the picker. Stale search
responses are discarded. No full run directory is downloaded. Selecting a run
loads its timeline and summary at the same high-water cursor. Refresh explicitly
to rebuild the view; there is no new background polling.

Usage includes reported tokens by purpose, model/tool call counts, accumulated
work, exclusive phase duration, average first output from known model samples,
and returned tool-result bytes. Missing values show `—`; a partial reported total
says `+ unknown`. Cache/reasoning counters are subsets. A truncated scan/group list
shows **Partial summary**, with coverage/counting details available on expansion.

Timeline pages load 100 records at a time (at most 1,000 retained in the browser).
The summary independently scans up to 4,096 records. Select a bar for timing,
usage, outcome and metadata, or select a tool in the Usage table to filter its
loaded timeline calls. Parallel spans retain their actual positions; absent
duration is a marker, not a zero-length measured action. High-water cursors bound
reads but do not freeze late commits or retention across requests.

Administrators can expand **Recorder & exporter health** for the serving API's
process counters. These are not aggregated across workers/API replicas and do not
prove database connectivity or lossless recording. Observer/runner credentials
cannot retrieve those process-wide counters. Credentials, database URLs and
exporter configuration are never returned by that endpoint.

Deploy API and UI from the same revision. Enable recording in the existing service
configuration and run explicit migrations as described in the
[chart guide](../../deploy/service/chart/README.md#optional-agent-recordings).
If recording is disabled or unavailable, run search remains usable; no sample data
or execution-database fallback replaces missing recording data.
[REST contracts](../../docs/harness/OBSERVABILITY-QUERIES.md).

### Browser qualification

With an installed Playwright module/browser and Docker running, the existing
owned-container qualification helper can also drive this UI against two disposable
PostgreSQL databases and the real Rust API:

```sh
OW_OBSERVABILITY_UI_BROWSER=1 \
OW_PLAYWRIGHT_MODULE=/absolute/path/to/playwright-or-playwright-test \
OW_OBSERVABILITY_UI_OUTPUT="$PWD/.ow-service-observability-ui-screens" \
  node deploy/reference/qualify-observability.mjs .ow-service-observability-ui-test
```

The state directory must be new and gitignored. The output contains deterministic
fixture screenshots and a sanitized UI report. No provider key is needed; no tools
execute. Add `OW_OBSERVABILITY_UI_IMAGE=sha256:YOUR_LOCAL_UI_IMAGE_ID` to test the
read-only Linux UI container instead of a host UI process. The image must already
exist locally; the fixture never pulls it. The container test uses Docker Desktop's
`host.docker.internal` to reach the temporary host API, with explicitly enabled
local HTTP. Production TLS verification is unchanged. Only the test's exact labeled
container and temporary connection-only configuration directory are removed.

The browser checks real scoped reads, separate recording storage, pagination,
unknown/empty records, disabled recording and service-token revocation. Delayed
searches and busy/transport/partial-coverage cases are injected at the browser
boundary and identified separately in the report. Screens are reviewed at 1440 px
and 390 px widths; no full Kubernetes rollout or cloud sizing is implied.
