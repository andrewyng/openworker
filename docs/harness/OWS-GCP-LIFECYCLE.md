# GCP lifecycle and independent cleanup

The lifecycle engine and cleanup package are locally qualified. **Real workload
creation is still blocked in code** until full cost, effective IAM, private-access
and live guard qualification are complete. This guide is for local review/testing,
not an instruction to launch a paid cluster.

## Where recovery runs

```text
ows CLI ──────────── Infrastructure Manager ─────── GKE / SQL / workload storage
   │                         ▲
   │                         │ observe / delete owned deployment
   ▼                         │
Control bucket ◄──── Cloud Run cleanup job ◄──── authenticated Cloud Scheduler
  source ZIP#generation         metadata identity
  immutable control digest     outside the workload cluster
  checkpoint + CAS generation
```

The control bucket and cleanup-image registry belong to a separate Terraform
foundation. Workload teardown cannot remove them. The cleanup runner is the Rust
`ows` binary; it needs no Python, gcloud, model key, desktop process or Kubernetes
connection. The CLI and runner execute the same recovery engine.

## Export locally

Use a fresh [infrastructure bundle](OWS-GCP-INFRASTRUCTURE.md) and prepared local
lifecycle state, with at most a four-hour TTL:

```sh
ows deploy gcp cleanup foundation --plan ./gcp-infrastructure --out ./gcp-control

docker build --platform linux/amd64 --target gcp-cleanup \
  -t openworker-gcp-cleanup:local -f deploy/service/Dockerfile .
```

The foundation contains the private, versioned control bucket, separate image
registry, deterministic `source.zip`, provider checksum lock and source manifest.
The ZIP contains exactly `main.tf.json` and `.terraform.lock.hcl`. Its CRC and file
contents are checked during qualification. Export/build does not install or upload
anything to GCP.

The next export needs the **actual object generation** of that source ZIP and the
**actual digest** of the runner image in the separate control registry. For local
schema tests only, use synthetic values; they will not pass real source checks:

```sh
ows deploy gcp cleanup package --state ./gcp-state \
  --source-generation 123 \
  --guard-image 'REGION-docker.pkg.dev/PROJECT/ow-PREFIX-control-images/ows@sha256:DIGEST' \
  --out ./gcp-cleanup
```

Replace all placeholders with the exact values from the foundation/source
receipts. Package rejects floating image tags, another repository/project,
malformed generations, expired plans, nested outputs and existing directories.

The package contains:

| File | Purpose |
|---|---|
| `main.tf.json` / provider lock | Independent Cloud Run job, exact invoker binding, OAuth-authenticated Scheduler |
| `control.json` | Versioned graph, source generation/digest, expiry, request UUID and runner-image digest |
| `checkpoint.json` | Initial durable lifecycle state; never overwrite an existing checkpoint |
| `package-manifest.json` | Exact content-addressed control URI and checkpoint key |
| `permissions-request.json` | Authority requirements; **not an implemented or sufficient IAM policy** |

Only local exports and Terraform `init -backend=false` / `validate` have been
qualified. The [installation commands](OWS-GCP-INSTALLATION.md) now automate bounded
source/control/checkpoint uploads and read-only reconciliation. Foundation and
job/Scheduler Terraform apply remain separate reviewed steps. Uploads use `ifGenerationMatch=0`, read back exact receipts and preserve
an existing checkpoint. A lost upload reply requires a read before any retry.
Never put credentials or passwords into Terraform inputs or control files.

## Runtime protocol

Before acting, the engine validates the control hash, plan digest, private bucket
project/privacy settings, exact ZIP generation and source digest. Cloud Storage
checkpoint writes use the last observed generation as a compare-and-swap
precondition. Competing writers cannot both commit an intent.

- Creation uses Infra Manager's **create-only REST method**, with a fixed persisted
  request UUID. It never uses gcloud's create-or-update wrapper or imports an
  existing deployment. The production creation authorizer currently refuses.
- Reconciliation binds the original acknowledged operation, target, verb, intent
  time window, deployment creation time, executor/source and revision. Lost replies
  can be recovered from a single matching provider operation plus those receipts;
  missing/ambiguous history needs review. A name, annotation or 404 alone is not
  ownership proof and never authorizes replay.
- Deletion persists intent before `DELETE`, with a separate stable UUID and
  `deletePolicy=DELETE`. It never uses `ABANDON`, `deleteState` or per-asset deletes.
  Failed/unknown deletes retain receipts and are not blindly repeated.
- Before deletion, capture IDs for discoverable GKE instances, attached/PVC disks,
  managed groups, instance groups and templates. After provider deletion, check the
  named workload roots and captured assets directly. Denial is not absence;
  replacement at a captured name blocks completion.
- Once those absence checks pass, persist a scheduler-pause intent, pause the exact
  trigger and confirm its state. An ambiguous pause is read-reconciled. This stops
  recurring executions while keeping control evidence available.

Cloud mode commands require explicit opt-in and scope acceptance:

```sh
ows deploy gcp lifecycle reconcile --control ./gcp-cleanup/control.json \
  --allow-experimental-cloud --accept DEPLOYMENT_ID
ows deploy gcp lifecycle destroy --control ./gcp-cleanup/control.json \
  --allow-experimental-cloud --accept DEPLOYMENT_ID
```

These are real API/checkpoint operations, **not dry runs**. Do not run them until
the control artifacts and required authority exist. CLI authentication obtains a
temporary setup-account token using the exact operator's existing gcloud login.
It does not persist that token. Cloud Run instead checks the expected cleanup
identity through the fixed Google metadata endpoint.

The job has one task, parallelism one, no task retries and a three-minute timeout.
Scheduler uses a fixed Google Run API target, OAuth identity and empty request body;
it cannot provide arbitrary job overrides. Health checks pin the job UID, image,
arguments, task identity/configuration, scheduler target and heartbeat. Overlapping
scheduled executions are fenced by checkpoint generations, not assumed impossible.

## Qualification boundary

Local tests cover the complete engine sequence, concurrent writers, dropped HTTP
and checkpoint replies, operation ambiguity, ownership/source/revision drift,
expiry without the Mac/GKE cluster, resource delays, dependent-ID capture and
scheduler-pause recovery. Transport tests use loopback-only HTTP and synthetic
tokens. Metadata/IAM behavior has not been live-qualified.

GKE label propagation, partial-creation inventory and unattached PVC detection need
a real GKE failure/teardown drill. Captured-asset absence is not proof that every
possible GCP dependent was discovered. The output therefore keeps
`managedDependentAbsenceQualified=false` and `allCleanupComplete=false`, even when
the observed workload is gone and its scheduler is paused. Control storage,
registry images, bootstrap IAM and billing evidence require a separate reviewed
decommission step. No automatic deletion of recovery evidence is implemented.

The $100 allowance is cumulative. Full pricing/prior-spend checks, resource-specific
executor/node/workload/cleanup permissions, private installer connectivity,
regional versions and live guard qualification remain mandatory before paid
workload creation. `--allow-experimental-cloud` does not bypass that gate.

[Ledger](OWS-GCP-TRIAL-PLAN.md) · [Bootstrap](OWS-GCP-BOOTSTRAP.md).

Primary references: [Infra Manager create](https://docs.cloud.google.com/infrastructure-manager/docs/reference/rest/v1/projects.locations.deployments/create),
[delete](https://docs.cloud.google.com/infrastructure-manager/docs/reference/rest/v1/projects.locations.deployments/delete),
[GCS generation preconditions](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert),
[Cloud Run scheduled jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
