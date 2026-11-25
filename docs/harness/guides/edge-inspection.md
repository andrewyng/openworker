# An inspection app for the edge

InspectionBuddy is a complete sample built with the OpenWorker Harness SDK.
An inspector uploads a part photo, reviews visible findings and suggested checks,
asks follow-up questions, and records a human decision. Six synthetic part photos
provide a starting point.

A factory can keep lightweight interfaces at its inspection stations while a
site-local server runs the agent sessions and stores inspection records. Operators
can reopen their work after closing the browser; the UI does not own the agent
process or provider credential.

```text
Inspection workstation               Site-local edge server
┌───────────────────┐               ┌───────────────────────────────┐
│ Browser + UI proxy├── HTTPS ──────►│ InspectionBuddy application   │
└───────────────────┘               │  ├─ photos + human decisions  │
                                    │  └─ Harness.connect()         │
                                    │        ▼                      │
                                    │ OpenWorker service + workers  │
                                    │  ├─ PostgreSQL sessions/traces│
                                    │  └─ OpenShell tool sandbox    │
                                    └──────────────┬────────────────┘
                                                   │ HTTPS
                                                   ▼
                                          Vision model provider
```

This sample uses cloud vision. The photo and context leave the site for inference;
edge-hosted orchestration is not offline inference. Model keys stay with trusted
harness workers. Your app owns user authentication and inspection authorization.

## Try it without Kubernetes

The same app can use a managed harness process on your computer:

```sh
cd harness/examples/expertbuddy
npm ci
cp .env.example .env
# Edit .env: supply OPENAI_API_KEY and a vision-capable OW_MODEL.
npm start
```

Open [localhost:4336](http://127.0.0.1:4336). Local mode uses a tool-free agent,
immutable image/reference attachments, SQLite sessions and traces, and a local
photo/record directory. It calls a real model. It does not require Docker,
OpenShell or a Kubernetes cluster.

The [example README](../../../harness/examples/expertbuddy/README.md) covers the
workflow, configuration, source map and tests. The directory retains its original
`expertbuddy` name; the sample app is branded InspectionBuddy.

## Move the agent to a service

Use the [edge deployment playbook](../../../harness/examples/expertbuddy/deploy/README.md)
to register the agent and RuntimeProfile, issue an owned operator credential, and
connect the application to your service over HTTPS. It includes a Kubernetes app
manifest; installing the OpenWorker service is a separate operator step.

A [local Docker service](../../../deploy/service/README.md) runs the service roles
and PostgreSQL in Compose but still needs a reachable OpenShell gateway backed by
Kubernetes. For a fully cluster-free app test, use the local managed mode above.
For service/OpenShell qualification, use [private Kubernetes](private-kubernetes.md).

## What to look for

- Photo-grounded findings and image annotations, distinct from the inspector’s final decision.
- Streamed agent activity and reported input/output tokens across follow-up turns.
- Saved photos, reports and decisions that survive app restarts.
- Explicit session recovery rather than automatic replay of uncertain requests.

The sample’s app store has one writer; keep one replica and retain its data volume.
Add shared storage, organization identity and per-user authorization for production.
Synthetic images and demo guidelines are not manufacturing acceptance criteria.
Continue with [attachments](attachments.md) and [observability](observability.md)
for the SDK capabilities behind this workflow.
