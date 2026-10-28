# Review an AWS infrastructure bundle

28 October 2026. This is an **offline export**, not a deployed service. `ows` still
refuses live apply/destroy. No AWS account was accessed to qualify these templates.

The generator now produces its own CloudFormation execution role and the platform
roles referenced by the infrastructure. The older option to supply an existing
execution role remains available for identity-only exports.

## Generate and review

```sh
ows deploy aws plan \
  --account 123456789012 --region us-west-2 --name my-service --budget 100 \
  --operator-arn arn:aws:iam::123456789012:role/operators/deployer \
  --infrastructure-config ./aws-infrastructure.json \
  --out ./aws-review
ows deploy inspect --plan ./aws-review
ows deploy validate --plan ./aws-review
```

Use [the example input](../../harness/service-cli/examples/aws-infrastructure.json)
to see the format. **Its versions, guard bucket, object version and digest are test
fixtures, not a qualified version catalog or an uploaded artifact.** Replace them
with actual, preflight-verified values before any future cloud deployment. The CLI
validates syntax, not online availability or account quotas. The next preflight
phase will resolve these choices; this explicit input is the review/test boundary,
not a requirement that the final installer ask customers to hand-pick every version.

Inputs specify two distinct zones in the target region, a canonical private /16,
Kubernetes and matching AL2023 ARM64 node release, PostgreSQL 16 minor version, all
five add-on versions, and an immutable S3 guard-code version plus SHA-256. Explicit
`acknowledgeExperimentDataDeletion: true` is required. The input has no field for
secret values; unknown fields are rejected. The input file is embedded in the plan,
so validation does not depend on the original file remaining available.

`--infrastructure-config` requires `--operator-arn` and conflicts with
`--execution-role-arn`. New infrastructure bundles use schema 3. Existing schema-1
and schema-2 bundles retain their original contents/digests and remain inspectable.

## What is generated

| Artifact | Contents |
|---|---|
| `bootstrap.cfn.json` | Setup, artifact, CloudFormation executor, cluster, node, EBS CSI and cleanup roles; five execution managed policies |
| `infrastructure.cfn.json` | Dedicated VPC/routing/NAT, private EKS/node group, pinned add-ons, private PostgreSQL and named secret, S3 and three ECR repositories |
| `guard.cfn.json` | Independent Lambda, log group, schedule and invocation permission; uses the supplied immutable code artifact |
| `execution-policy-*.json` | Network creation, network management, EKS, data and guard permissions |
| `setup-policy.json`, `workload-policy.json`, `cleanup-policy.json` | Separate operator, artifact and cleanup grants |
| `permission-review.json` | Coverage of every infrastructure/guard resource, broad-grant explanations and remaining qualification checks |
| `guard-contract.json` | Required health, identity, expiry and ownership checks; explicitly unqualified |
| `bootstrap.sh`, `BOOTSTRAP.md`, `REVIEW.md` | Customer-executed identity creation and review details |
| Plan, inputs, resource graph, manifest | Target, fixed expiry, exact artifact hashes and installation order |

Planning, inspecting and validating invoke no cloud executables and read no cloud
credentials. The exported script is an explicit IAM mutation if a customer chooses
to run it later; it is **never executed by `ows plan`**. Locally we ran it only with
a fake AWS executable.

## Network and data choices

Two fixed `t4g.large` nodes run in private subnets. The cluster management API has private access; its public endpoint is disabled.
A single NAT gateway in the first zone provides outbound access. This costs money
and is a single point of failure; it is an experiment topology, not HA production.
The installer needs a network path to the cluster management API (private access). No VPN, public
application ingress or silent API exposure is added to solve that connectivity.

PostgreSQL is private, encrypted and configured to require TLS. Its password is
generated in a specifically named Secrets Manager secret and resolved by
CloudFormation; no password value is present in the plan or outputs. This differs
from the older experiment's RDS-managed secret: it confines secret permissions to a
known deployment name rather than granting across the `rds!db-*` namespace. Automatic
password rotation is not configured for this disposable experiment.

Deletion-on-expiry is explicit: database backups/snapshots are not retained on
teardown or replacement. The artifact bucket blocks public access, requires TLS,
and grants application access to a deployment-specific prefix. No workload or
model provider credentials are embedded in images or templates.

## Permission boundaries

The customer bootstrap creates identities. The CloudFormation executor **cannot edit
IAM roles or policies**, and can pass only the exact cluster/node/pod/cleanup roles
to their intended services. Setup can use that executor only for the experiment's
infra and guard stacks. It cannot modify the identity stack. Cleanup can delete the
infra stack but cannot create/update stacks or remove its own guard/bootstrap.

Five managed policies keep execution permissions below IAM's individual policy size
limit. Every infrastructure/guard resource must have a known permission domain;
adding an unrecognized type makes generation fail rather than silently granting
all service actions. This coverage check is not proof that a cloud resource handler
needs no further permissions; simulation and live creation/rollback still matter.

Broad grants are called out in the bundle:

- EC2 generated resource IDs require type/account/region ARN patterns. Create calls
  require the deployment request tag; subsequent mutations require ownership tags.
  Parent-resource permissions are separate. Ownership tags cannot be reassigned or
  removed through the generated tag-management permissions.
- EKS `CreateCluster` requires `Resource: "*"`. Region, ownership tag, API auth and
  private-endpoint conditions restrict it. They do **not** enforce a cluster-count
  or spending limit. Subsequent APIs are scoped to the planned cluster/children.
- EC2 Describe and CloudWatch DescribeLogGroups return regional metadata without
  per-resource scoping. Secrets Manager GetRandomPassword returns generated randomness,
  not existing credentials. KMS DescribeKey reads key metadata only in this account/
  region; the executor gets no arbitrary KMS decrypt/write authority.
- Platform cluster/node/CSI roles use explicitly listed AWS-managed policies, whose
  upstream content is a separate review boundary. Node CNI permissions support initial
  cluster bootstrap; untrusted pods must not be admitted until metadata isolation,
  network enforcement and OpenShell boundaries have passed platform qualification.
- The setup identity receives this cluster's platform administration access entry
  because OpenShell installation needs cluster-wide CRDs/controllers/storage/CNI.
  Runtime UI and agent identities do not inherit that access.

Service-linked roles for EKS, node groups, Auto Scaling and RDS are prerequisites.
Preflight must report missing roles and customer-bootstrap actions; the executor
is not granted general IAM creation to make those failures disappear.

References used during review: [EC2 authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html),
[EKS authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html),
[RDS authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_rds.html),
[CloudFormation secret references](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html).

## Guard qualification remains mandatory

The guard is outside the target cluster/VPC and pins a versioned S3 package. A hash
in the plan is not proof that the object exists or implements safe cleanup. Before
billable infrastructure can be launched, the executor must verify its bytes, install
it, and prove the behaviors in `guard-contract.json`:

- No action before the fixed expiry; verify deployment/account/region identity.
- Resolve the exact infra stack ARN and check ownership before deletion.
- Delete only the deployment's artifact prefix and owned available volumes; refuse
  foreign objects, versioned buckets or ambiguous ownership.
- Preserve operation identity across lost responses and report incomplete cleanup.
- Keep guard/bootstrap resources until teardown is independently verified.

The schedule disables event-delivery retries; that does not remove the need for
reconciliation on the next scheduled invocation. The cleanup implementation and
its failure tests belong to the next lifecycle milestone. This export does not
claim that an old cleanup script can be dropped in unchanged.

## Local verification and remaining work

Run the Rust CLI tests:

```sh
cargo test --offline --manifest-path harness/Cargo.toml -p openworker-service-cli
```

Optionally lint generated templates with `cfn-lint==1.58.0`:

```sh
cfn-lint --ignore-checks W3010 --template \
  ./aws-review/bootstrap.cfn.json \
  ./aws-review/infrastructure.cfn.json \
  ./aws-review/guard.cfn.json
```

Only W3010 is waived: the bundle deliberately pins reviewed availability zones for
one target account/region. Other schema, dependency and IAM action-name checks remain
on. Tests also check policy limits/boundaries, template dependency graphs, private
network/data settings, invalid inputs, artifact tampering, no cloud calls and the
fake-AWS bootstrap flow. These are not AWS IAM simulation or deployment tests.

Read-only identity/version/quota/price/artifact [preflight](OWS-AWS-PREFLIGHT.md) is
now implemented. Next: durable lifecycle reconciliation and guard packaging/qualification. Then connect image upload, exact
secret handoff, artifact service-account wiring and OpenShell/service Helm phases.
The experiment budget still needs a verified estimate and independent expiry guard;
no local check converts it into a hard spending cap. Live provisioning remains disabled.
