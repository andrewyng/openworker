# GCP infrastructure export and lifecycle preparation

This is the local G2a step. It exports a Terraform graph and prepares a durable
lifecycle journal. **It does not provision infrastructure or install cleanup.**
Production workload creation remains gated. The current cloud reconciliation and
owned-deployment cleanup commands are in the [lifecycle guide](OWS-GCP-LIFECYCLE.md).

## Export the workload graph

Start with a fresh, reviewed GCP bootstrap bundle from the
[bootstrap guide](OWS-GCP-BOOTSTRAP.md), with a TTL of at most four hours. Supply
an explicit JSON input file:

```json
{
  "zone": "us-central1-a",
  "kubernetesVersion": "1.33.5-gke.1162000",
  "terraformVersion": "1.5.7",
  "googleProviderVersion": "6.44.0",
  "nodeMachineType": "e2-standard-2",
  "nodeDiskGb": 40,
  "postgresVersion": "POSTGRES_16",
  "postgresTier": "db-f1-micro",
  "postgresDiskGb": 10,
  "subnetCidr": "10.70.0.0/16",
  "podCidr": "10.72.0.0/14",
  "serviceCidr": "10.76.0.0/20",
  "controlPlaneCidr": "172.16.0.0/28",
  "sqlCidr": "10.80.0.0/16"
}
```

The GKE version above is a **schema-test example**, not a verified regional
offering. G2b must resolve availability of exact Terraform/GKE versions and node
images before apply. The exported provider version is a locally schema-qualified
compatibility baseline, not an instruction to upgrade to the latest provider.

```sh
ows deploy gcp infrastructure --plan ./gcp-bootstrap \
  --config ./gcp-input.json --out ./gcp-infrastructure
ows deploy validate --plan ./gcp-infrastructure
```

The new immutable bundle preserves the deployment identity and expiry. It adds
`gcp-main.tf.json`, the provider checksum lock, the resolved resource graph,
`gcp-cleanup-contract.json` and its review notes. Unknown input fields, arbitrary
modules/provisioners, unqualified provider pins, oversized disks, noncanonical or
overlapping/non-private ranges and mismatched zones are rejected before export.

The twelve Terraform resources define a dedicated VPC/subnet/router/NAT, private
service peering, private zonal GKE Standard cluster and fixed two-node AMD64 Ubuntu
pool, private PostgreSQL instance/database, artifact bucket and image registry.
Calico enforcement and its control-plane add-on are explicit. The transient default
node pool also uses the dedicated node identity, rather than the project's default
Compute identity. GKE creates additional managed instances/disks/firewall rules;
the twelve Terraform addresses are not a count of all eventual cloud resources.

The private management endpoint requires installer VPC connectivity. There is no
public fallback. Ubuntu alone does not establish OpenShell's kernel/runtime
compatibility, and network-policy enforcement needs a real negative-connectivity
test. This trial topology does not provide production HA/backup qualification.
No database password or model key is written into the graph.

## Validate locally without cloud resources

Use a separate working directory so Terraform never edits the immutable export:

```sh
mkdir ./gcp-schema-check
cp ./gcp-infrastructure/gcp-main.tf.json ./gcp-schema-check/main.tf.json
cp ./gcp-infrastructure/gcp-provider.lock.hcl ./gcp-schema-check/.terraform.lock.hcl
cd ./gcp-schema-check
# Use the pinned Terraform 1.5.7 executable.
terraform init -backend=false -input=false -lockfile=readonly
terraform validate -json
```

`init` downloads the pinned signed provider; `validate` checks its real schema
locally. Do not run Terraform plan/apply here. Schema validation does not prove
effective IAM/organization constraints, quota, available versions, prices, private
connectivity or compatibility with OpenShell. The deploy CLI does not invoke
Terraform automatically or turn a schema pass into `applyReady=true`.

## Prepare durable lifecycle state

```sh
ows deploy gcp lifecycle prepare --plan ./gcp-infrastructure --out ./gcp-state
ows deploy gcp lifecycle status --state ./gcp-state
```

Preparation writes a private, fresh, fsynced journal outside the immutable bundle.
It binds the bundle digest, target resource name and stable nonzero request UUID.
Status validates that binding under a local lock and reports **local state only**;
it does not contact Google. Both commands report `applyReady=false`.

Infrastructure Manager exposes a resource name and creation time, **not a
deployment UID**. The transport must persist the acknowledged operation and bind
the creation/revision receipts before declaring ownership. A name/label match or
404 following a lost reply is insufficient to adopt, recreate or delete resources.

| Persisted phase | Next recovery decision |
|---|---|
| Prepared | Wait for complete preflight and independent-guard qualification |
| Create intent/unknown, no acknowledged operation | Require ownership reconciliation; never automatically replay |
| Create intent/unknown with operation receipt, or creating | Observe the original create |
| Active | Observe owned resources |
| Delete intent/unknown, no acknowledged operation | Require review; never automatically repeat delete |
| Delete intent/unknown with receipt, or deleting | Observe the original delete |
| Destroyed | Keep receipts; no further action |

These are implemented decision rules, not a cloud executor. The bounded provider transport, durable
remote checkpoints and independent runner now have local qualification in the
[lifecycle guide](OWS-GCP-LIFECYCLE.md). Full cloud preflight/install and live
metadata/IAM/cleanup qualification remain outstanding.

## Independent cleanup boundary

The workload graph intentionally excludes the durable source/state bucket and
cleanup runner/scheduler. Deleting GKE must not delete the component or authority
that finishes teardown. The proposed G3 implementation is an authenticated Cloud
Scheduler trigger of a digest-pinned Cloud Run Job, using the dedicated cleanup
identity and durable control receipts. Its role must target the owned Infra Manager
deployment/control objects, without workload creation or arbitrary IAM authority.
No cleanup authority or scheduled job has been installed yet.

Before workload creation, G3 must prove the guard is installed, healthy and able
to reconcile an interrupted create/delete without the Mac or GKE cluster. Keep
Terraform state, source and operation receipts until underlying resources are
confirmed absent. Do not use `ABANDON` or `deleteState` as cleanup. Trial teardown
intentionally deletes the workload artifact bucket and **all object versions**;
control evidence is retained separately. Bootstrap accounts/grants require a
separate explicit revocation procedure after infrastructure teardown.

The $100 cumulative allowance remains unchanged. Costs are unresolved; the current
bootstrap report reserves $25. NAT, temporary default nodes, database/storage,
cleanup execution and retained control data must be counted before paid apply.

[Trial ledger](OWS-GCP-TRIAL-PLAN.md).

Primary references:
[Infra Manager Deployment fields](https://docs.cloud.google.com/infrastructure-manager/docs/reference/rest/v1/projects.locations.deployments),
[create and request IDs](https://docs.cloud.google.com/infrastructure-manager/docs/reference/rest/v1/projects.locations.deployments/create),
[delete policy](https://docs.cloud.google.com/infrastructure-manager/docs/reference/rest/v1/projects.locations.deployments/delete),
[GKE network-policy enforcement](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/network-policy),
[NVIDIA OpenShell support matrix](https://docs.nvidia.com/openshell/latest/reference/support-matrix.html).
