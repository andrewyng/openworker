# Read-only AWS preflight

The `ows deploy aws preflight` command checks a schema-3 infrastructure bundle
against AWS and writes a fresh report tied to its deployment ID and bundle digest.
It does not provision infrastructure, install policies or bootstrap identities.

```sh
ows deploy aws preflight \
  --plan ./aws-review \
  --profile customer-login \
  --out ./aws-preflight
```

Use AWS CLI v2 with the customer's existing login/profile or environment credential
provider. The CLI authenticates as the operator ARN already pinned in the plan; a
wrong account or operator stops the run after the identity check. No administrator
credential is distributed to the service or its agents.

The output directory must be new and outside the plan bundle. `preflight.json` is
created with mode 0600 in a mode-0700 directory on POSIX systems. The plan remains
immutable. Windows ACL behavior is not qualified. JSON is also written to stdout.
Exit 0 means the required observations passed; exit 1 means one or more checks are
blocked, or a local validation error prevented the run. A blocked report contains
fixed error codes and sanitized findings; raw provider stdout/stderr are not copied
into it. Credential values and guard bytes are never part of the report.

## Checks

| Check | Evidence gathered |
|---|---|
| Operator identity | Exact AWS account and IAM operator identity; IAM role paths are handled through the assumed-role identity |
| Existing prerequisites | EKS, node group, Auto Scaling and RDS service-linked role ARNs |
| Zones and capacity offering | Selected zones enabled/available; t4g.large offered in both; no capacity reservation |
| Kubernetes | Pinned version currently in EKS standard support |
| Node release | Pin matches the current recommended AL2023 ARM64 release; Amazon-owned ARM64 AMI available |
| Add-ons | Each pinned build advertised as ARM64-compatible with the pinned Kubernetes version |
| PostgreSQL | Version available with postgres16 parameters; encrypted gp3 db.t4g.micro orderable in the selected zones |
| Quota headroom | Regional standard vCPUs, EKS clusters, VPCs, EIPs, NAT gateways and RDS instance quota/usage observations |
| Costs | Current On-Demand USD rates for two nodes, DB instance, EKS standard cluster, zonal NAT, public IPv4 and node/DB gp3 storage |
| Guard artifact | Same-region, expected-owner bucket; exact version and bounded object bytes verified against SHA-256 |
| Policies | AWS Access Analyzer validation of the eight generated identity policy documents |
| Freshness | Plan remains unexpired; report validity is bounded to 15 minutes from the first observation |

Pins are never silently updated. If a node/add-on pin is stale, the report includes
reviewable supported alternatives. Regenerate and review a new plan to change them.
The first node-release check supports only the current recommendation; a historical
release can remain valid on AWS but is conservatively blocked by this implementation.
The checked-in input is a test fixture and may intentionally fail live version checks.

AWS API pagination is handled explicitly, including RDS `Marker` and service-specific
next-token names. Repeated tokens, malformed responses, more than 20 pages or more
than 10,000 rows block the affected check. A partial inventory must not be treated
as complete quota headroom. Inventory counts every non-host On-Demand instance's
default vCPUs, even if its family actually consumes a separate quota. It also counts
unused active reservations and the full allocation of assessing/scheduled/pending/
delayed reservations, because they consume quota before instances run. NAT usage is
counted across the region against the per-zone ceiling. Both choices can reject
conservatively. Observed headroom and offerings are not reservations and can change.

## Cost interpretation

Seven fixed-rate components come from AWS Price List `GetProducts`; the request
endpoint is us-east-1 while product filters use the deployment's region. Selected
SKU/unit/rate/quantity are retained in the report, including the source unit label
and normalized hourly unit (`Hrs`, `Hours`, `hours` and `hour` refer to hours). Running RDS instance usage is
selected separately from unused-reservation products, and zonal NAT separately from
regional NAT. Zero, missing, invalid or ambiguous matching rates block the check;
they never become a zero estimate.

The estimate uses the full planned experiment lifetime, rounds fixed costs upward
to cents, and prorates storage with a documented 730-hour month. A default $25
planning reserve covers variable charges:

```sh
ows deploy aws preflight --plan ./aws-review --out ./another-preflight \
  --variable-allowance-usd 30
```

The report blocks if fixed estimate plus reserve exceeds the plan's authorized
budget. It retains the estimate so the operator can see the reason. The reserve is
**not an upper bound**: NAT traffic, transfer, T4g CPU credits, artifact/image storage,
requests, logs and OpenShell workspace disks can exceed it. Existing account spending,
credits, discounts, tax and enterprise agreements are not reconciled here. Neither
this estimate nor the user's $100 experiment authorization is a hard spending cap.

## Optional setup-role check

After customer identity bootstrap, add:

```sh
ows deploy aws preflight --plan ./aws-review --out ./post-bootstrap-check \
  --profile customer-login --check-setup-role
```

This assumes only the exact setup role derived from the plan, with a 900-second
session policy allowing `sts:GetCallerIdentity` only. The CLI verifies the assumed
account/role. Credentials stay in process memory and are cleared after the check;
they are not printed or saved. The AWS CLI subprocess necessarily receives them in
its environment. An explicit source profile is removed for that subprocess so it
cannot override the temporary credentials.

This proves role assumption and identity, **not** that the role has effective
infrastructure permissions. Access Analyzer policy validation is also distinct from
IAM simulation and live creation/rollback tests. The optional check is not required
before identities exist; the report says `not_requested` when it was omitted.

## Time, output and credential boundaries

The command has an explicit allowlist of read-only APIs plus temporary STS role
assumption. No generic cloud-command forwarding, shell strings, secret-value reads,
policy installation or deployment calls are exposed.

```sh
ows deploy aws preflight --plan ./aws-review --out ./bounded-preflight \
  --timeout-seconds 20 --overall-timeout-seconds 300
```

Per-call deadline: 1–60 seconds. Overall deadline: 1–600 seconds. AWS SDK retries are
disabled for these CLI invocations. Credential helper behavior still belongs to the
customer's trusted AWS CLI configuration. Configured endpoint overrides are ignored;
ordinary TLS verification remains enabled. EC2 instance metadata credential fallback
is disabled, so provide an explicit configured login/credential source.

Each subprocess has bounded stdout/stderr and is terminated on timeout. Raw buffers
are not logged; temporary STS credentials are zeroized when discarded. The guard
object must be 1 byte–8 MiB; head/version/owner checks precede a bounded ranged GET.
The downloaded file is private and removed on both success and failure. It is hashed,
never unpacked or executed. This does **not** qualify the cleanup behavior of that
package. `awsCliInvocations` counts CLI processes, not billing requests or internal
credential-provider calls.

## What a passing report permits

`checksPassed: true` describes observations, and `applyReady` remains **false**.
The report is local evidence, not an authorization token, signature or proof against
an operator who can rewrite local files. It must not be used as a way to bypass
fresh scope/authentication checks in a future executor.

Still required: independent guard behavior/health qualification, durable apply and
cleanup reconciliation, runtime image digests, connectivity to the cluster management API (private access),
image upload/secret handoff, OpenShell strict network enforcement and service/UI
installation qualification. Live provisioning remains disabled on all providers.

Sources: [EKS version API](https://docs.aws.amazon.com/eks/latest/APIReference/API_DescribeClusterVersions.html),
[recommended AMIs](https://docs.aws.amazon.com/eks/latest/userguide/retrieve-ami-id.html),
[RDS orderable options](https://docs.aws.amazon.com/cli/latest/reference/rds/describe-orderable-db-instance-options.html),
[S3 versioned downloads](https://docs.aws.amazon.com/cli/latest/reference/s3api/get-object.html).
