# AWS lifecycle and service installation

The native `ows` CLI coordinates a reviewed AWS infrastructure bundle and the shared
Kubernetes installer. The authorized AWS trial reached a real-model agent run on
private EKS + RDS + authenticated OpenShell: file/shell effects, UI traces and exact
PostgreSQL-backed event replay after both API replicas restarted. See the
[dated evidence](evidence/aws-service-agent-20261029.json) and
[trial ledger](OWS-AWS-TRIAL-PLAN.md) for exact repairs and qualification limits.
The cloud path remains experimental and requires explicit opt-in. A clean fresh
install with the final CLI, S3 workload transfers and healthy-deployment expiry
cleanup still need qualification; the successful trial required controlled operator
repairs, now covered by generator fixes and regression tests.

The ordinary `ows deploy apply/destroy` entry points remain closed. The explicit
`ows deploy aws lifecycle` commands below are the experimental execution path.
GCP and Azure remain offline planners.

A separately authorized [AWS trial](OWS-AWS-TRIAL-PLAN.md) is now in progress.
Its scoped identity/guard stacks and private infrastructure have been submitted;
live agent and installation qualification are not yet complete.

## Prepare the prerequisites

Use a current CLI built from this branch:

```sh
cargo build --locked --manifest-path harness/Cargo.toml -p openworker-service-cli
```

On the operator machine: AWS CLI v2, Helm 3 with install labels, kubectl, local
Unix-socket Docker, and tar. Image publication needs Docker API 1.48 or later for
platform-specific save and 1.46 for push. The current reference uses Linux ARM64
nodes and images. [Docker save](https://docs.docker.com/reference/cli/docker/image/save/),
[Docker push](https://docs.docker.com/reference/cli/docker/image/push/).

Review the [infrastructure bundle](OWS-AWS-INFRASTRUCTURE-EXPORT.md) and
[preflight](OWS-AWS-PREFLIGHT.md). Before producing the final immutable plan:

1. Package this CLI's bundled expiry guard with `ows deploy guard package --out DIR`.
   An authorized artifact publisher must upload that exact ZIP to a versioned,
   private S3 location, separately from the deployment's application artifact bucket.
   Record the returned object version and package SHA-256 in `guardCode`.
   The CLI does not create that publisher identity/bucket or upload the guard.
2. Resolve and review versions, zones, quotas and the cost estimate using preflight.
   The example infrastructure JSON contains fixtures, not deployable artifact IDs.
3. Establish the route from the operator host to the future cluster management API
   (private access): for example an existing VPN or a host inside the VPC.
   The installer does not enable a public management endpoint.
4. If you do not already have a private network route, the infrastructure input can
   include `"operatorAccess": {"mode":"ssm","amiId":"PINNED_AMAZON_ARM64_AMI"}`.
   It creates a private t4g.micro bridge with an encrypted 8 GiB disk and no inbound
   ports/public IP. Its role allows only regional SSM heartbeat/session channels;
   it cannot read Parameter Store, database, artifacts or model credentials.
   The main stack/expiry cleanup owns the instance; bootstrap owns its role/profile.
   Preflight includes its AMI/offering, extra vCPUs and fixed instance/storage cost.
   The operator still needs a Session Manager plugin and permission to start a
   port-forwarding session to that instance. Keep the original EKS TLS host/CA;
   an operator-side CONNECT bridge can carry it over SSM without disabling TLS.
5. Review and run the generated identity bootstrap using the authorized operator
   login. Configure an AWS CLI role profile for the exact generated `*-setup` role,
   using that existing login as its source. Never install setup credentials in pods.

The setup identity can read only the generated database bootstrap secret, verify the
guard, publish to three exact ECR repositories, update this cluster's CNI before
expiry, and coordinate its stacks. It cannot read model credentials or alter
bootstrap identities. ECR login requires `Resource: *`, but repository write
permissions remain separately scoped. The workload artifact role is attached only
to the API/worker service account; UI, controller, platform controller, bootstrap
and sandbox workloads do not inherit it. Artifact keys begin with the deployment ID,
matching the generated S3 prefix policy.

## Create and resume infrastructure

These commands are **instructions for a future authorized deployment**, not evidence
that a cloud deployment has been run.

```sh
ows deploy aws lifecycle prepare --plan ./aws-review --out ./cloud-state
ows deploy aws lifecycle apply --state ./cloud-state \
  --accept-plan BUNDLE_DIGEST --profile my-ows-setup \
  --preflight ./preflight/preflight.json --allow-experimental-cloud
ows deploy aws lifecycle status --state ./cloud-state --profile my-ows-setup
```

Apply performs one bounded transition; repeat the
same command after observing progress until `phase: infrastructure_ready`. A fresh,
matching preflight report is required immediately before each **new** stack creation.
It may need refreshing after the guard is created. An expired/missing report does
not prevent reconciliation or status of an already dispatched request.

The independent guard is installed first. Before creating infrastructure, the CLI
checks its exact packaged code digest, role, runtime, environment, enabled schedule,
target, zero retry configuration, reserved concurrency and synchronous read-only
health result. Lambda HTTP 200 alone is not health: a `FunctionError` blocks progress.
[Lambda invoke](https://docs.aws.amazon.com/cli/latest/reference/lambda/invoke.html).

Keep the same state directory. Each intent is saved before dispatch with a stable
request token. Reconciliation checks original stack ARN, stack-level event token,
template, executor and ownership tags. A matching name is insufficient. Failed,
rolled-back, replaced or changed stacks stop for operator review. CloudFormation
[creation tokens](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateStack.html)
are observed in [stack events](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_DescribeStackEvents.html).

If a request may have been sent but no stack is visible, **do not repeat creation**
with a new state directory. After independently establishing that no effect occurred,
an operator can pass `--confirm-no-effect guard` or `--confirm-no-effect infra`.
The previous intent stays in recovery history. Access denied, expired credentials
and malformed provider replies never count as absence.

## Publish reviewed images

Build/review local service, runner and optional management-UI images. Publish each:

```sh
ows deploy aws lifecycle publish-image --state ./cloud-state \
  --profile my-ows-setup --role harness --source my-service:reviewed \
  --allow-experimental-cloud
# Repeat with --role runner and, if enabled, --role service-ui.
```

Use each returned digest URI in Helm values. The publisher verifies Linux ARM64,
the original stack's immutable repository and local image configuration. It pushes
one platform manifest to a unique immutable tag. A lost response is reconciled by
tag, manifest digest and original configuration; no automatic second push.
Explicit `--confirm-no-effect` is available only after independently proving an
uncertain push had no effect.

Temporary tar/config files are private and removed on return, including failure.
A hard process kill can leave the temporary Docker auth directory until the next
publication cleans it. Protect the state directory and remove such private remnants
after a killed invocation; never archive them with public logs. ECR tokens are
short-lived provider credentials. [ECR authorization](https://docs.aws.amazon.com/AmazonECR/latest/APIReference/API_GetAuthorizationToken.html).

## Bind the Kubernetes installation

Prepare the same installer config described in [local installation](OWS-LOCAL-INSTALLATION.md),
with these cloud differences:

- `service.namespace: "ow-service"`, `release: "ows"`, `fixtureDatabase: false`.
- `openshell.mode: "install"` with the pinned NVIDIA OpenShell 0.1.3 archive/values
  in a dedicated namespace, or `"existing"` with explicit namespace/release/fullname.
  Reused gateways are checked and never adopted or uninstalled.
- Service config `service.id` and artifact `deployment` equal the cloud deployment ID.
  Disable legacy compatibility and loopback plaintext DB access. Set database CA path
  `/run/service/pg-ca.crt` and S3 bucket/region from the original stack outputs.
- Use returned repository digest URIs for harness/runner/UI. API TLS server names,
  gateway endpoint/TLS names and optional UI origin must match the actual services.
- API/model/DB credential values stay out of Helm values, images and plans.

Export the pinned upstream core controller, encrypted ownership-tagged EBS storage,
and trusted-component network policies **offline**:

```sh
ows deploy aws lifecycle platform-artifact --plan ./aws-review \
  --out ./platform.json \
  --openshell-namespace openshell --openshell-release ow-shell
```

The two OpenShell flags must match the installer config; omit both only when
providing separately reviewed gateway networking. Set `platform` in that config to
`{"file":"/absolute/path/platform.json","sha256":"RETURNED_SHA256"}`.
The exporter uses Agent Sandbox 1.0.6 with the locally tested ARM64 controller digest.
It does not download mutable manifests or apply anything.

The artifact allows trusted service/bootstrap, controller, DNS, CSI-controller and
selected gateway pods to communicate. It deliberately does not claim isolation
between those trusted roles. Untrusted OpenShell tool pods do not match these rules;
their tool permissions and networking are separate. Review any additional sandbox
bootstrap/egress requirements and qualify them on EKS before admitting user jobs.
Do not substitute namespace-wide allow rules for per-sandbox controls.

```sh
ows deploy aws lifecycle installation --state ./cloud-state \
  --profile my-ows-setup --config ./installer-input.json --out ./handoff
ows deploy cluster plan --config ./handoff/installer-config.json --out ./install-state
ows deploy cluster platform --state ./install-state --accept-plan INSTALL_DIGEST \
  --allow-experimental-cloud
# Observe/repeat platform until platform_ready; a CNI update may still be progressing.
ows deploy cluster apply --state ./install-state --accept-plan INSTALL_DIGEST \
  --openshell-only
```

The handoff pins the original EKS ARN/VPC/private endpoint/CA, repository outputs,
S3 scope and service identity. Its kubeconfig executes `aws eks get-token` using the
explicit setup profile; it contains no cloud credentials. The shared installer also
pins the live cluster UID, artifact/chart/values hashes and namespace UIDs.

Platform preparation creates only reviewed, previously absent resources and saves
UID receipts. Existing shared resources are not overwritten/adopted: use the
existing-platform path instead, supplying any needed policies through your own
reviewed deployment. A fresh cluster requires the Agent Sandbox controller, EBS
CSI/Pod Identity add-ons and a single encrypted, tagged EBS default StorageClass.
The platform command installs policies before switching the original VPC CNI from
standard to strict mode. Lost update replies are observed, not repeated.

## Hand off Secrets privately

Create a mode-0600 JSON file, with PEM strings as values:

```json
{
  "database": {"pg-ca.crt": "REVIEWED RDS CA PEM"},
  "apiTLS": {"tls.crt": "SERVER CERT PEM", "tls.key": "SERVER KEY PEM", "ca.crt": "CA PEM"},
  "model": {"anthropic": "KEY SUPPLIED PRIVATELY"}
}
```

Omit `model` if unused; otherwise declare a matching `secrets.model` name in Helm
values so the worker mounts it. The cloud path obtains the exact original
Secrets Manager database password internally, verifies host/database/user identity
and builds a driver-compatible `sslmode=require` URL; the mandatory Verified TLS
connector checks CA and hostname using the separately supplied RDS bundle; do not supply `database-url` in cloud input.
The RDS CA must be independently obtained/reviewed for the target region.

```sh
ows deploy cluster secrets --state ./install-state --input ./private-secrets.json \
  --accept-plan INSTALL_DIGEST
ows deploy cluster apply --state ./install-state --accept-plan INSTALL_DIGEST
ows deploy cluster status --state ./install-state
```

Gateway client TLS is copied directly from the authenticated gateway privately.
Bootstrap/readiness tokens are generated once in private installer state. Secrets
are create-only, nonce/UID-bound and reconciled after lost replies; existing or
changed Secrets are never silently overwritten. Their values do not enter the journal.
Kubernetes Secrets and installer private files are still sensitive; use appropriate
disk/etcd encryption and retention. Readiness tokens expire and need operational
rotation for longer-lived deployments.

If Helm timed out, it may have replaced its description. Installer request IDs in
release labels plus release UID/revision identify the original operation. After
inspection, `--reconcile-ready-release service-release` checks **that original
hook-free release**, successful bootstrap Job and all enabled rollouts, without
reinstalling it. Hook-bearing or changed releases require operator recovery.

## Shut down in order

Close sessions, cancel/finish admissions and queued turns, and delete environments
through the service first. Then:

```sh
ows deploy cluster destroy --state ./install-state --deployment-id INSTALL_DEPLOYMENT_ID
ows deploy aws lifecycle destroy --state ./cloud-state --profile my-ows-setup \
  --deployment-id CLOUD_DEPLOYMENT_ID --installation-state ./install-state \
  --allow-experimental-cloud
```

The trusted installer checks aggregate PostgreSQL state, freezes API intake and
checks again before uninstalling. Session/admin API credentials cannot call this
aggregate inspection directly. Once the shared installer records its first mutation intent, the cloud coordinator
requires and verifies the completed installation teardown receipt, live cluster identity and absent owned namespaces,
plus an empty owned unversioned artifact bucket, before infra deletion.
Exporting a handoff alone creates no teardown obligation. An unstarted native plan
with no operation intents can be closed without installation;
a missing namespace after a possible mutation requires inspection.

Delete requests use their original token/ARN and are not replayed after connection
loss. Observe/repeat destroy until `infrastructure_deleted`. That phase does **not**
prove every cloud resource, EBS volume or charge is absent. Shared platform resources
remain on service-only teardown. The independent expiry guard/bootstrap are retained;
verify stack/resource/volume absence and final charges separately, then remove those
identities and the guard last. No automatic guard removal is provided.

Expiry blocks new provisioning, publication and Kubernetes installs; status and
cleanup remain available. Expiry and the $100 budget are not a hard spending cap.

## Qualification evidence

See [the progress ledger](OWS-CLOUD-EXECUTION-PLAN.md) and
[local evidence](evidence/cloud-lifecycle-20261028.json). Fake-provider tests exercise
cloud protocol/recovery; kind tests exercise installation and sandbox effects.
The [AWS run evidence](evidence/aws-service-agent-20261029.json) records actual IAM
handler/rollback fixes, private connectivity, strict CNI probes, encrypted EBS tags,
immutable ECR publication, managed RDS TLS and agent/UI/restart checks. Pod Identity
mapping is provisioned; actual S3 transfers, healthy-deployment expiry cleanup and
final cost reconciliation remain explicitly unqualified.

The bootstrap is exported as compact JSON and checked against CloudFormation's
[51,200-byte inline template limit](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateStack.html)
before a bundle is produced. Human-readable permission documents remain separate.
SSM forwarding prerequisites are described in [AWS Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html).

## Reconcile scheduled expiry after the cluster is gone

The expiry guard records its original infrastructure ARN and delete request in its
own DynamoDB control table. After scheduled cleanup finishes, reconcile that
external request without sending another delete or contacting Kubernetes:

```sh
ows deploy aws lifecycle reconcile-expiry --state ./cloud-state \
  --profile my-ows-setup --deployment-id <originalDeploymentId>
```

The command refuses before expiry. It verifies the original guard stack, code,
configuration and schedule; reads the exact control key consistently; matches the
original stack template/creation identity and stack-level expiry delete token; and
requires the owned bucket and all deployment-tagged volumes to be absent. It writes
only a local observed-delete receipt. Pending deletion, changed identity, a different
manual delete request, incomplete inventory or a remaining volume blocks success.
The guard and bootstrap remain available for independent verification and retirement.
No API-node/Kubernetes receipt is required after the cluster has actually vanished.

The setup policy grants `dynamodb:GetItem` on its one deployment control key and
regional `ec2:DescribeVolumes` metadata reads for this observation. It cannot edit
that record or delete disks. A fresh identity bundle is required for this added read
scope; existing immutable bundles remain usable with their original CLI for cleanup.
