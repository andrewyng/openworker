# Cloud deployment identities and bootstrap

28 October 2026. Companion to [the deployment spec](OWS-DEPLOYMENT-SPEC.md).

The customer should review one bundle, run a bootstrap step using their existing
cloud login, and let `ows` coordinate the deployment. Cloud-specific implementations
are intentional. Helm remains the application installation layer on every cloud.

## Current implementation

| Capability | AWS | GCP | Azure |
|---|---|---|---|
| Offline typed plan, resource graph, inspect/validate/status | Implemented | Implemented | Implemented |
| Generated setup/workload identity policies and bootstrap | Generated-role infrastructure export; imported-role option retained | Identity-only export implemented; [guide](OWS-GCP-BOOTSTRAP.md) | Design below |
| Automatically generated infrastructure execution permissions | Implemented offline; not cloud-qualified | Not yet | Not yet |
| Read-only cloud preflight | Implemented; [guide](OWS-AWS-PREFLIGHT.md) | Bootstrap/direct IAM checks qualified; infrastructure checks pending | Not yet |
| Provisioning and cleanup | Experimental opt-in; locally tested recovery, AWS trial pending | Disabled | Disabled |

The original design milestone was offline. Subsequent cloud qualification is
recorded in the provider ledgers. GCP identity bootstrap and read-only bootstrap
preflight are now live-qualified in the [GCP ledger](OWS-GCP-TRIAL-PLAN.md), with
infrastructure apply still disabled. Azure remains plan-only. Generated artifacts
or fake-CLI tests alone do not qualify real IAM or infrastructure behavior.

## Separate the authorities

```text
Customer's existing authorized login
    │ grants reviewed permissions once
    ▼
Setup identity ──► infrastructure executor ──► cloud resources
    │                                      └─► narrowly scoped workload binding
    └─► explicit Kubernetes platform / namespace installation permissions
                                                    │
                           artifact component ──────┘
                           UI and agents get no setup identity
```

“Setup” and “workload” are two permission categories, not a requirement for exactly
two physical identities. CloudFormation's execution role, GCP's Terraform executor,
node identities and an expiry-cleanup identity may each need distinct permissions.
The service's own administrator token is separate from all of them.

Creating a role does not confer authority on the person running bootstrap. The
customer must already have permission to create those identities and assign those
specific grants. No static AWS keys, GCP service-account keys or Azure client secrets
are generated. Customer CI may run the same reviewed artifacts.

## AWS: generated infrastructure and identity-only exports

The [infrastructure export](OWS-AWS-INFRASTRUCTURE-EXPORT.md) now generates the
CloudFormation executor and platform identities along with private EKS, RDS, network,
S3/ECR and guard templates. It takes `--infrastructure-config` plus `--operator-arn`.
The explicit experimental [lifecycle path](OWS-AWS-LIFECYCLE.md) is locally qualified;
actual cloud provisioning, guard cleanup and IAM qualification remain pending. The rest of this
section describes the retained **imported execution-role, identity-only** path.


```sh
ows deploy aws plan --account 123456789012 --region us-west-2 \
  --name my-service --budget 100 --out ./aws-plan \
  --operator-arn arn:aws:iam::123456789012:role/operators/deployer \
  --execution-role-arn arn:aws:iam::123456789012:role/reviewed/cfn-executor
ows deploy inspect --plan ./aws-plan
ows deploy validate --plan ./aws-plan
```

The two optional ARN arguments must be supplied together. Without them, this is an
ordinary offline plan. With them, it additionally exports:

- `bootstrap.cfn.json`: two named IAM roles; no cluster, database, bucket or instances.
- `setup-policy.json`: exact owned stack names, exact execution-role PassRole,
  and DescribeCluster for the proposed cluster.
- `workload-policy.json`: artifact objects under one deployment prefix, bounded
  bucket listing and bucket location.
- `bootstrap.sh`: customer-run identity creation, never invoked by the planner.
- `BOOTSTRAP.md`: required caller permissions, scope, limitations and cleanup.

The customer reviews those files before a future authorized run of
`bash ./aws-plan/bootstrap.sh --apply-identities`. This is an IAM mutation, even
though it does not launch a cluster. We have **not** run it against AWS.

The script first validates the bundle/expiry, verifies the AWS account and exact
operator identity, then verifies the supplied execution role ARN exists. IAM users
and assumed IAM roles are supported, including IAM paths. Root, arbitrary account
trust, STS session ARNs as configuration, wildcard identities and cross-account
inputs are rejected. Cross-account federation can be added explicitly later.

The script creates a uniquely named bootstrap stack using a stable client request
token. It neither updates nor adopts an existing stack. AWS mutation retries are
disabled. An error or lost response requires inspection of that exact stack before
a retry; the script does not switch identities, generate a new deployment ID, or
claim rollback happened. A failed completion wait also leaves status unresolved.

### Execution role prerequisite

This export is the **bring-your-own execution-role path**. It deliberately does not
claim to implement the full one-command infrastructure bootstrap yet. The supplied
role must trust CloudFormation and have customer-reviewed resource permissions.
Existence/ARN checks do not audit its policies or certify its sufficiency.

A restricted setup role can still direct CloudFormation to use everything the
execution role permits. Stack-name scoping does not neutralize an overprivileged
execution role. This is why we do not substitute AdministratorAccess to make a demo
work. AWS documents this [CloudFormation service-role boundary](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-iam-servicerole.html).

The generated setup role may operate only the deployment's `-infra` and `-guard`
stacks, and must specify the supplied execution role on create/update/delete.
PassRole is restricted to that exact role and `cloudformation.amazonaws.com`.
Creation/update grants expire at the plan deadline. Observation and teardown grants
remain available for cleanup; actual cleanup still needs an independent guard.
It cannot edit IAM, upload images, fetch secrets or modify its own bootstrap stack.
These additional installer phases need separate reviewed grants in the next stage.

### Workload binding

The artifact role trusts EKS Pod Identity only with the planned cluster ARN,
namespace `ow-service`, and service account `ows-artifacts`. Its policy grants the
planned S3 bucket and `<deployment-id>/` object prefix. The bucket, Kubernetes
service account and Pod Identity association are **not created by bootstrap**.
Future Helm changes must attach that identity only to artifact-using components.
The present chart does not yet wire this new service account.

EKS session tags must remain enabled; they carry the cluster, namespace and service
account attributes used by these conditions. See [Pod Identity trust](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-role.html)
and [session tag attributes](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-abac.html).
The wildcard suffixes cover stack-generated IDs and objects inside the deployment
prefix. No generated allow statement uses a bare `*` resource or wildcard action.

### AWS execution-policy review and qualification

The auto-generated path now maps template resources to explicit permission domains.
The following boundaries still require cloud preflight/simulation and live qualification;
local generation does not prove every resource-handler permission is sufficient:

| Resource/phase | Required authority and review boundary |
|---|---|
| VPC, subnets, routing, security groups | Exact owned resources; creation-time tag controls where supported; explicit explanation of APIs that cannot scope resources |
| EKS, node groups, launch templates and add-ons | Exact cluster/node names; bounded capacity; exact cluster/node/CSI roles and passed-to-service constraints |
| PostgreSQL, subnet/parameter groups | Owned database and networking; generated secret identity resolved and recorded; private connection path |
| ECR images | Exact repositories; image uploader separate from infrastructure lifecycle; justify any global authentication-token action |
| S3 and encryption | Exact bucket/object scope; TLS/public-access controls; KMS grant only if selected |
| Workload identities | Exact Pod Identity association and service-account tuple; bootstrap-owned roles cannot be edited by setup |
| Database/model credentials | Explicit secret references and designated readers; no values in plan, stdout or Terraform/ARM parameters |
| Guard and cleanup | Independently authenticated, deployment-scoped cleaner installed and verified before billable resources |
| Kubernetes/OpenShell | Explicit platform privileges for CRDs/controllers/storage/CNI; namespace-scoped service install afterward |

Service-linked roles and account prerequisites must be detected and either installed
by the customer bootstrap or reported; no surprise organization-wide grants.
Permission simulation, actual resource creation permissions and cleanup behavior
still require separate authorized AWS qualification.

## GCP: plan implemented, identity/installer design

October 30 implementation update: `ows deploy gcp bootstrap` exports the explicit
identity setup, and `ows deploy gcp preflight` verifies required direct bindings and
optionally pins account UIDs to creation receipts. See the
[command guide](OWS-GCP-BOOTSTRAP.md). The broader flow below remains the target:
resource-specific execution grants, Terraform lifecycle and independent cleanup
are not implemented yet. Bootstrap readiness never implies infrastructure readiness.

```sh
ows deploy gcp plan --project my-ows-project --location us-central1 \
  --name my-service --budget 100 --out ./gcp-plan
```

Use an existing customer-selected project with billing enabled. `ows` will not
silently create projects or attach billing accounts. Project number is resolved by
read-only preflight; it is not inferred from the project ID. Initial topology targets
GKE Standard, Cloud SQL PostgreSQL, Artifact Registry, Cloud Storage and OpenShell.
AMD64 is a planning default; compatible images and regional machine availability
must be qualified before application.

Proposed bootstrap flow:

1. Verify the logged-in caller and selected project; show required API enablement,
   organization policies, quota and network prerequisites.
2. Customer creates a setup service account, a separate Infrastructure Manager
   executor account, and dedicated workload/cleanup principals. The caller may
   impersonate only the setup account; no downloadable JSON keys.
3. Setup receives Infrastructure Manager deployment operations and permission to
   act as the exact executor account. The executor receives resource-specific
   permissions for the resolved Terraform resource graph. Avoid Owner/Editor and
   project-wide service-account impersonation. IAM-binding changes stay with the
   bootstrap administrator, except explicitly reviewed workload bindings.
4. Infrastructure Manager applies pinned Terraform modules and records deployment,
   revision and operation IDs. Private state/artifact storage gets separate access
   from application artifacts. Service-agent prerequisites and configuration-bucket
   access must be included in preflight; enabling the API alone is insufficient.
5. Workload Identity Federation for GKE binds the exact workload principal to its
   artifact bucket. Select direct principal grants or an explicitly bound IAM
   service account based on client-library support. Keep node and installer identities
   out of application pods.

Impersonation permission and the executor's provisioning permissions are different:
[Infrastructure Manager authentication](https://docs.cloud.google.com/infrastructure-manager/docs/authentication),
[executor service account](https://docs.cloud.google.com/infrastructure-manager/docs/configure-service-account).
The generator will pin the permissions associated with a tested module version.
Name/label conventions alone cannot enforce project-wide create permissions; where
IAM cannot isolate creation by deployment, a dedicated project or customer-managed
network scope is required and must be stated in the review.

The cluster management API (private access) needs an explicit operator/installer network path.
Do not make the API public to work around connectivity. The expiry guard needs an
independent identity and lifecycle from the GKE cluster being destroyed. Terraform
state remains until teardown is verified; a failed operation is reconciled using
its existing operation ID, not a fresh deployment.

## Azure: plan implemented, identity/installer design

```sh
ows deploy azure plan \
  --tenant 11111111-2222-4333-8444-555555555555 \
  --subscription 66666666-7777-4888-8999-aaaaaaaaaaaa \
  --location westus2 --name my-service --budget 100 --out ./azure-plan
```

Use Bicep compiled to ARM at a dedicated resource-group scope. Target AKS, PostgreSQL
Flexible Server, ACR and Blob Storage. Resource group creation is an explicit
bootstrap operation; ordinary deployment authority is scoped to that group. Azure
resource names have different limits (e.g. storage accounts); derive actual names
from the deployment ID during template generation, not by blindly using the common
human-readable prefix. Initial AMD64 capacity is a proposal pending qualification.

Proposed bootstrap flow:

1. Verify tenant, subscription and caller object identity from the existing login.
   Ensure resource-provider registrations, organization policies and quotas are
   satisfied. Do not silently switch subscriptions or enable unrelated providers.
2. Customer grants a dedicated setup principal the exact deployment/resource actions
   in the resource group. CI uses federation; deployments from Azure compute may use
   managed identity. No generated client secret. The existing interactive login can
   execute the same reviewed ARM/Bicep artifacts.
3. Customer bootstrap handles specific role assignments and federated identity
   credentials. The ordinary installer must not receive unrestricted role assignment
   write permission, subscription Owner, or rights to expand its own roles. Passing
   a managed identity is an explicit assignment permission, not implicit in its name.
4. AKS's control-plane and node/kubelet identities remain distinct from the artifact
   workload identity. Registry pulls receive only the required repository access.
5. For artifact access, bind the exact AKS OIDC issuer, Kubernetes subject
   `system:serviceaccount:ow-service:ows-artifacts` and token audience
   `api://AzureADTokenExchange` to a dedicated user-assigned managed identity.
   Assign Blob data rights at the intended container scope; no storage account keys.

[AKS workload identity setup](https://learn.microsoft.com/en-us/azure/aks/workload-identity-deploy-cluster)
documents the issuer/subject/audience binding. Cloud resource management and Kubernetes
RBAC remain separate; see [Azure container roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/containers).
Namespace permissions alone cannot install OpenShell's cluster-wide components.

Persist ARM deployment and operation IDs plus exact resource IDs. ARM incremental
mode is not a teardown strategy; cleanup must inventory owned resources explicitly.
Deployment stacks may help manage lifecycle, but deny settings and delete semantics
need explicit review before choosing them. Never delete a shared resource group or
AKS node resource group based on its name. Guard identity and scheduling must survive
loss of the target AKS cluster. Blob content retention and database backups require
an explicit cleanup decision.

## Next implementation order

1. AWS read-only preflight is implemented; use it to validate and review deployment
   inputs before progressing to the lifecycle phase.
2. Mocked lifecycle/reconciliation tests and guard packaging/qualification; retain
   disabled live apply until separately authorized.
3. Wire the current OpenShell/service/UI Helm installation and cloud workload identity.
4. Qualify AWS with the experiment budget, then implement GCP and Azure bootstrap /
   template executors against the same lifecycle contract and their own IAM tests.

All clouds must reject wrong scope before mutation, preserve stable IDs across
interruptions, keep credential values out of artifacts, and distinguish unknown
outcomes from failures known to have caused no side effects.
