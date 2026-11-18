# InspectionBuddy

**A second set of eyes for the assembly line.**

A sample application built with the OpenWorker Harness SDK. Photograph a part,
review the AI’s visible findings and suggested checks, ask follow-up questions,
and record the inspector’s decision. Six synthetic photos let you try damaged
and healthy-looking parts without supplying your own images.

The app is called **InspectionBuddy**; its source lives in
`harness/examples/expertbuddy/`. It demonstrates two ways to use the same SDK:

| Try it | Where the agent runs | What you need |
| --- | --- | --- |
| **Local development** | Managed Rust harness process alongside the Node app | Node.js 20.19.5+ and an OpenAI key. No Docker or Kubernetes. |
| **Edge deployment** | OpenWorker service on a separate server | An existing service, scoped credentials and persistent app storage. |

## Try it locally — no cluster

From the repository root:

```sh
cd harness/examples/expertbuddy
npm ci
cp .env.example .env
```

Edit `.env` to supply `OPENAI_API_KEY` and a vision-capable `OW_MODEL` that your
account can use. The SDK installs its matching native runtime automatically;
no Rust compilation is required. Keep optional npm dependencies enabled for
this mode. Then:

```sh
npm start
```

Open [localhost:4336](http://127.0.0.1:4336). Select a sample or upload a PNG,
JPEG or WebP under 8 MB, add inspection context, and select **Assess this photo**.
Review the annotations, open **Agent activity** for progress and token usage,
ask a follow-up, and **Record your decision**. Find saved work in **Inspection log**.

```text
Browser on this computer
        │ localhost:4336
        ▼
Node app ─────────────── .data/ photos + inspection records
        │ SDK over private stdio
        ▼
Managed Rust harness ── .data/harness/ sessions + SQLite traces
        │ HTTPS
        ▼
OpenAI vision API
```

This mode uses a real model and a tool-free agent. `sandbox: "off"` is appropriate
here because it registers no filesystem, shell or other tools. The Node app saves
photos; the model receives immutable image and reference-guide attachments.
The browser never receives the model key. Photos and context **are sent to the
cloud model**; this example does not implement offline inference.

`npm start` reads `.env`; process environment variables take precedence. `PORT`
changes the browser port, and `DATA_DIR` changes the persistent data directory.
Use a distinct `DATA_DIR` for each local harness or remote service; saved session
references belong to that backend and are not migrated when you switch modes.
Local mode binds to loopback and rejects cross-origin requests. It is a development
entry point, not a public multi-user service. Keep one app process per data directory.
Keep the same model and agent definition when reopening existing state; use a new
`OW_AGENT` name or a fresh `DATA_DIR` after changing the immutable definition.

## Why deploy the agent at the edge?

A factory may have several inspection workstations and cameras but one site-local
server. Centralizing agent sessions and inspection records there lets operators
close a browser, switch workstations or reconnect without moving the agent loop
onto each device. The interface remains lightweight; execution and storage stay
with the site’s infrastructure. A configured cloud model provides vision inference
in this sample; moving orchestration to the edge does not make inference local.

```text
               FACTORY / SITE NETWORK
┌──────────────────────┐       ┌────────────────────────────────────┐
│ Inspection stations  │ HTTPS │ Edge server                        │
│ Browser + UI proxy   ├──────► │ InspectionBuddy application API    │
│ Camera / photo input │       │   ├─ photos + inspection records   │
└──────────────────────┘       │   └─ Harness.connect()             │
                               │            │ HTTPS + scoped token │
                               │ OpenWorker service                 │
                               │   API / controller / agent workers │
                               │   ├─ PostgreSQL sessions + traces  │
                               │   └─ OpenShell → tool sandbox      │
                               └────────────────┬───────────────────┘
                                                │ HTTPS; worker-held key
                                                ▼
                                      Configured vision provider
```

The workstation talks to your application API, not directly to the harness or
provider. The app holds an **owned-scope operator credential**; only trusted
harness workers receive model keys. Service administrators register the agent
and RuntimeProfile separately. The production topology supports several stations;
the sample itself uses one shared app credential and a single-writer file store.
Add organization login, user authorization and shared storage before wider rollout.

### Connect the workstation to an edge application

Create a separate, ignored `.env` for the workstation:

```dotenv
INSPECTIONBUDDY_MODE=frontend
PORT=4336
EDGE_API_URL=https://inspection-api.example.com
EDGE_APP_TOKEN_FILE=/absolute/private/app-token
```

Run `npm ci --omit=optional` and `npm start`. The workstation needs only the
application token. It does not need a model key, harness operator token, native
harness binary or Kubernetes access. A loopback SSH tunnel is also supported
with `EDGE_API_URL=http://127.0.0.1:18447`; non-loopback upstreams require HTTPS.
For a private service CA, set `NODE_EXTRA_CA_CERTS` before starting Node.

See [edge setup and deployment](deploy/README.md) for service registration, app
configuration, secret placement and the Kubernetes application manifest.

## Can I run an OpenWorker service locally without Kubernetes?

The [local Docker service playbook](../../../deploy/service/README.md) runs the
OpenWorker API, controller, workers and PostgreSQL in Docker Compose. **It still
requires a reachable OpenShell gateway backed by Kubernetes**, plus matching
runner artifacts. It is useful if you already operate that gateway elsewhere,
but it is not a standalone Kubernetes-free service installation.

For a completely cluster-free InspectionBuddy test, use **local development**
above. It exercises the published SDK, real attachments, sessions, progress and
usage through a managed harness process. It does not test service authorization,
RuntimeProfile provisioning, worker failover or Kubernetes/OpenShell isolation.
Use the [private Kubernetes guide](../../../docs/harness/guides/private-kubernetes.md)
when you want to qualify those service boundaries.

## What the example demonstrates

- Image and UTF-8 reference attachments, followed by questions in the same session.
- Streamed journal activity and aggregate model tokens across the assessment and follow-ups.
- Schema-validated findings and bounded image annotations; model output is rendered as text.
- Durable photos, reports, inspector decisions and separate agent session references.
- Recovery of saved runs without blindly replaying uncertain requests.

App records use atomic file replacement and a single writer. Run one replica with
a persistent volume and a Recreate strategy. Harness session and trace storage is
separate: SQLite locally, PostgreSQL in the service deployment. Usage may be
unavailable or partial when recording is disabled or a provider omits counts.

AI observations never automatically approve a part. The synthetic images and
[demo reference guides](server/catalog.mjs) are illustrative, not production
acceptance criteria. A photo cannot establish hidden weld integrity, electrical
continuity or calibrated dimensions. An inspector records the final disposition.
Image provenance: [asset hashes](sample-provenance.json) and
[generation prompts](sample-prompts.md).

## Tests and source map

```sh
npm test
# Explicit opt-in: calls the running app and incurs real model usage.
# Set INSPECTIONBUDDY_URL if the app is not on localhost:4336.
npm run test:live
```

The automated suite covers input/report validation, annotation bounds, durable
records, idempotency, authorization, cross-origin writes, human-only decisions,
usage accounting and recovery. Deterministic test adapters are confined to tests.
The live check submits a healthy-looking panel and a defective weld and verifies
saved reports, photo retrieval and nonzero recorded model usage.

| File | Responsibility |
| --- | --- |
| [server/local-agent.mjs](server/local-agent.mjs) | Local `Harness.start()`, tool-free agent and persistent traces. |
| [server/agent.mjs](server/agent.mjs) | Edge `Harness.connect()`, attachments, streaming, recovery and usage. |
| [server/engine.mjs](server/engine.mjs) | Inspection lifecycle, progress and follow-ups. |
| [server/store.mjs](server/store.mjs) | Photos, records and inspector decisions. |
| [server/index.mjs](server/index.mjs) | Local app, authenticated edge API or workstation proxy. |
| [public/app.js](public/app.js) | Inspection UI, annotations, history and decisions. |

This is repository example source, not part of the SDK/runtime npm packages.
