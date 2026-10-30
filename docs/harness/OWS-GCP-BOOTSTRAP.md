# GCP bootstrap export and read-only preflight

These commands prepare and inspect the identities for a GCP deployment. They do
not install a cluster or launch an agent. GCP infrastructure lifecycle and automatic
cleanup remain separate implementation gates in the [trial ledger](OWS-GCP-TRIAL-PLAN.md).
The next local step is the [infrastructure export and lifecycle preparation](OWS-GCP-INFRASTRUCTURE.md).

Use the Rust toolchain from this checkout to build `ows`:

```sh
cargo build --locked --manifest-path harness/Cargo.toml -p openworker-service-cli
export PATH="$PWD/harness/target/debug:$PATH"
```

You also need a trusted Google Cloud CLI installation with the intended account
signed in, and an explicitly selected project with billing enabled. The commands
pin their project, billing/quota project and account; they do not change global
gcloud configuration. They do not download service-account keys or read model keys.

## Export

Create the offline plan, then supply the **project number** and exact operator
member when exporting identity bootstrap:

```sh
ows deploy gcp plan \
  --project my-gcp-project --location us-central1 \
  --name my-service --budget 100 --ttl-hours 4 --out ./gcp-plan

ows deploy gcp bootstrap \
  --plan ./gcp-plan --project-number 123456789012 \
  --operator-member user:developer@example.com --out ./gcp-bootstrap

ows deploy validate --plan ./gcp-bootstrap
```

Both exports are offline and refuse an existing output directory. The identity
bundle preserves the original plan/deployment/expiry and contains a new verified
manifest, `gcp-identities.json`, `GCP-BOOTSTRAP.md` and `gcp-bootstrap.sh`. Existing
plain GCP/Azure and AWS plan formats remain readable. Only GCP plans accept GCP
identity metadata. Groups, domains, wildcard principals, malformed project numbers,
tampered files and expired/future-dated plans are rejected.

The project number is **declared** during offline export. Online preflight and the
script verify it against Google's project metadata before using cloud identities.
Export never verifies a quota or estimates a price.

## Review and apply identity setup explicitly

The five generated same-project accounts have distinct purposes:

| Account | Bootstrap authority |
|---|---|
| Setup | `roles/config.admin` and `roles/serviceusage.serviceUsageConsumer` in the selected project |
| Terraform executor | `roles/config.agent` in that project; no GKE/Compute/SQL provisioning grants yet |
| Artifact workload | No grants yet; future artifact-bucket scope only |
| Cleanup | No grants yet; independent cleanup lifecycle is not installed |
| GKE nodes | No grants yet; separate from application artifact identity |

The exact operator receives `roles/iam.serviceAccountTokenCreator` **on setup's
service-account resource only**. Setup and the project's Google Infrastructure
Manager service agent receive `roles/iam.serviceAccountUser` **on the exact
executor resource only**. No Owner/Editor or project-wide impersonation role is
generated. Setup's Infra Manager administration is project-wide, so use a dedicated
project when that scope would otherwise include unrelated deployments.

Review the emitted files and the required API list, which includes Resource
Manager, Cloud Billing, Service Usage, IAM/credentials, Infra Manager and Cloud
Build. Enable those APIs as an explicit administrative setup action outside
preflight. The script explicitly initializes Google's Infrastructure Manager
service identity before creating the five customer-managed accounts. That step
requires the official gcloud beta component; install it ahead of time if needed:

```sh
gcloud components install beta --quiet
```

An administrator
with the reviewed account/IAM authority may run:

```sh
bash ./gcp-bootstrap/gcp-bootstrap.sh --apply-identities /private/fresh-journal
```

The script validates the bundle, caller, active project, project number and API
inventory before creation. It refuses generated accounts that already exist,
including accounts left by a partial run, rather than adopting or overwriting
them. A private fresh journal stores each confirmed result. Every mutation step
revalidates plan integrity and expiry.

A lost response has an unknown outcome. The script stops without its own automatic
mutation retry. Reconcile the exact account and journal before deciding how to
continue; gcloud can have its own internal transport retries. Fixed service-account
IDs prevent duplicate account creation. Plan expiry does **not** revoke previously
granted IAM bindings. Retain receipts for cleanup; this milestone installs no
automatic cleanup job.

For an interrupted bootstrap with all five creation receipts present, pin live
account IDs to that private journal during recovery:

```sh
ows deploy gcp preflight --plan ./gcp-bootstrap \
  --bootstrap-journal /private/creation-journal --out ./gcp-recovery-check
```

A missing/malformed receipt fails before cloud reads; a replaced account UID fails
its identity check. If all IDs match, inspect the exact live project/service-account
policies against the reviewed bindings in `gcp-identities.json`. An authorized
operator can apply **only confirmed-missing approved bindings**. A failed write
must be reconciled again before another attempt. Do not rerun account creation,
blindly repeat grants, or adopt a partial account by name. Full automated bootstrap
resume is not implemented in this milestone.

Google-managed service-account IAM metadata can be inaccessible to a project
owner. This does not justify granting access to Google's internal project. Bootstrap
retains the Service Usage creation receipt and preflight checks the exact reviewed
executor trust; it does not demand broader service-agent IAM read privileges.

## Run read-only preflight

```sh
ows deploy gcp preflight --plan ./gcp-bootstrap --out ./gcp-check \
  --timeout-seconds 20 --overall-timeout-seconds 120 \
  --spent-usd 0 --variable-allowance-usd 25
```

`preflight.json` is written in a fresh private directory outside the immutable
bundle. `bootstrapReady` means the required checks below matched; **`applyReady`
remains false** in this milestone. Missing prerequisites produce a nonzero exit
and a report with fixed error codes; provider stdout/stderr is not copied into it.

Required observations:

- Trusted default Google API configuration and the exact configured operator,
  followed by an authenticated project ID/number/active-state check.
- Billing linkage, enabled API inventory and required bootstrap APIs.
- Generated accounts, their unique IDs/enabled state, reviewed direct project
  roles and exact service-account trust. Unexpected or conditional bootstrap
  trust requires review. Project roles outside this bootstrap's allowlist on
  generated accounts are blocked.

If their APIs are enabled, regional CPU quota and available GKE versions are also
observed. Those observations do not reserve capacity, pin a supported version or
qualify node OS/OpenShell compatibility. Disabled APIs are reported and never
enabled by preflight. No token/secret export or IAM/resource mutation is allowed
through its command boundary.

Preflight clears inherited access-token/credential-file variables and rejects
configured endpoint overrides, implicit impersonation and other Google universes.
The process uses fixed argument arrays, null stdin, disabled prompts, per-operation
and overall deadlines, bounded output and fixed failure codes. Invocation counts
are CLI process counts, not HTTP request counts; gcloud can retry reads internally.

Direct IAM bindings are not proof of effective inherited permissions, conditions,
deny policies or organization constraints. Those, private management connectivity,
the resolved Terraform graph, image/version compatibility, complete quotas/prices
and independent expiry/cleanup still gate any future infrastructure apply.

`--spent-usd` is an explicit declaration of prior trial costs, **not measured
billing**. The report keeps actual spend and cost estimate unknown, subtracts the
declared spend plus reserve from the authorized plan allowance, and rejects an
exhausted allowance before cloud calls. Alerts-only budgets are not hard spending
caps. A fresh resolved cost and remaining-allowance check is required before paid
infrastructure.

## Qualification

Local tests run exported scripts against fake gcloud only. They check healthy and
blocked observation paths, scope, tampering, output/deadline limits, secret-marker
scrubbing, unknown mutation outcomes and refusal to adopt partial identities.
The [ledger](OWS-GCP-TRIAL-PLAN.md) separately records live observations and any
explicitly authorized cloud setup. A local fixture pass does not certify GCP IAM
or cluster behavior.

Primary references:
[Infrastructure Manager service accounts](https://docs.cloud.google.com/infrastructure-manager/docs/configure-service-account),
[access control](https://docs.cloud.google.com/infrastructure-manager/docs/access-control),
[budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets).
