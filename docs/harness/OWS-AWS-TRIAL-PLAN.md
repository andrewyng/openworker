# AWS service qualification trial

28 October 2026. Branch `feat/openworker-service-m1`, starting at `9f78b8a2`.
User authorized the next AWS trial with the existing $100 experiment budget.
Inventory/report precedes provisioning. Never modify unrelated account workloads.

## Goal

Submit a versioned agent release to OpenWorker Service on AWS, observe durable
progress events through `ows`/SDK and the management UI, and verify actual OpenShell
file/shell effects. Use deterministic infrastructure/network probes first, followed
by one small live run using the previously authorized existing model credential.
The installed service keeps one reviewed live-model configuration throughout;
prior local qualification already covers the deterministic agent path.
Credentials are not printed or committed.

## Order and gates

1. Read-only inventory, existing spend and cluster/access prerequisites. Use an
   explicitly read-only temporary STS session in ocw-demos; no reusable EKS cluster.
2. Add optional private SSM operator access to the generated infrastructure so the
   trial does not require a public cluster management endpoint or SSH. AMI pinned,
   no ingress/public IP, IMDSv2, encrypted disposable disk, SSM-only role, same main
   stack and expiry cleanup. Existing bundles without this option keep their shape.
   Check image/offerings/vCPU headroom/cost in preflight; test/lint before provisioning.
3. Prepare a fresh immutable review bundle with current versions, exact packaged
   guard uploaded to a new private versioned artifact location, and a fixed expiry.
   Review the final cost estimate and resource scopes. Customer login creates only
   the generated identity stack; the generated setup role drives service provisioning.
4. Install/verify independent guard first, then main infrastructure using the durable
   coordinator. No automatic replay of uncertain mutations, name adoption, public
   endpoint fallback or broad permission workaround.
5. Establish IAM-authorized SSM forwarding and a local CONNECT bridge restricted to
   the original TLS endpoint. Keep the generated kubeconfig endpoint/CA intact.
   Install pinned platform/networking/OpenShell, publish reviewed images to owned
   ECR, stage exact private DB/API/model Secrets and install service/UI.
6. Qualify network allow/deny, storage ownership, workload identity, agent submission,
   event replay, real sandbox tool effects, restart/control paths and one live-model
   task. Provide the user a local management UI or CLI session to inspect progress.
7. Record sanitized evidence, exact resources, estimates/actual observations and
   expiry. Tear down test sessions/environments and unused access/resources.
   Retain the guard until actual stack/volume/resource absence is verified; remove
   bootstrap identities and guard last. If leaving a demo temporarily, document its
   fixed expiry and how to stop it. The budget is not a hard spending cap.

## Progress

- Fresh read-only inventory: no EKS clusters in successful queried regions; one
  regional EKS read was unavailable. Oregon has no instances, RDS, NAT gateways,
  volumes or SSM managed hosts. Only the default VPC and unrelated buckets remain.
  Original CloudFormation inventory call needs a corrected read-only permission.
- Existing month-to-date account cost is approximately $318; it predates this trial
  and is not treated as this trial's $100 incremental authorization.
- Mac has ~200 GiB available. Reuse existing Docker/Rust caches and local images.
- Optional private operator access implemented and tested: pinned Amazon ARM64 AMI,
  private t4g.micro with encrypted 8 GiB disk, no ingress or public address, five-action
  regional SSM heartbeat/channel policy, no parameter or workload-secret reads.
  Preflight accounts for six standard vCPUs and the extra instance/storage.
- 53 CLI tests pass, including the added access template, AMI/offering/quota and
  cost tests. Generated templates pass cfn-lint (only intentional W3010 waived).
- Fresh AWS preflight passes all required checks with the published exact guard ZIP:
  53 AWS CLI invocations, ten identity policies validated with zero findings, 24-hour
  fixed estimate $7.67 plus $25 reserve = $32.67. This is not a spending cap.
- Created one dedicated private/versioned guard-code bucket and published one
  checksum-verified immutable code version. Unrelated buckets were not touched.
  The code publisher stack/bucket are tracked separately and retained until the
  actual guard/infrastructure are removed.
- Generated identity bootstrap dispatched once using the original operator role;
  waiting for its CloudFormation result. Native lifecycle state prepared separately.
  No cluster, node or database was created at this checkpoint.
- Existing Anthropic credential accepted by its model-list endpoint; no key printed.
  The signed/notarized AWS Session Manager plugin was unpacked locally, without a
  system-wide installation. Current reference pins are resolved from live AWS APIs.

### Live bootstrap limit found and fixed

The first identity CreateStack was rejected by AWS before resource creation: the
pretty-printed bootstrap exceeded the 51,200-byte inline TemplateBody limit.
Read-only checks independently confirmed that exact stack plus setup/executor roles
were absent. The original intent/failed plan were retained, never automatically
replayed. The exporter now emits compact bootstrap JSON and enforces the byte limit
before producing a bundle, with default/SSM regression checks. New plan v2 is
34,641 bytes, has a fresh ID/expiry, and reuses the already published exact code
version. No cluster, instance or DB has been launched yet.

### Current live state

Plan v2 identity stack is CREATE_COMPLETE. The first v2 bootstrap attempt had a
temporary STS credential-process failure; exact stack and setup/executor role reads
proved absence before an explicit unchanged-token retry. STS credential issuance
now has a bounded retry; no resource mutation acquired automatic retries. The
independent guard create was then dispatched by native `ows` under the generated
setup role, with ARN/intent recorded in `cloud-state-v2`. Main infra is not yet created.

The installed AWS CLI uses a fixed error prefix/retry annotation for absent resources.
Native absence parsing now recognizes those exact wrappers while still requiring the
exact error code, operation, selector and reason; permission/auth failures remain
fail-closed. Added unit and binary regressions pass.

Source packaging also caught a missing Docker build-context inclusion for the embedded
guard/platform files. Corrected `.dockerignore`, added a source-only build target,
and verified the source image includes all required assets. No trial credentials
enter the image context.

### AWS handler qualification and explicit recovery

The real CLI guard health check now passes (packaged digest, role, schedule,
concurrency, target, HTTP result and structured healthy payload). AWS CLI v2 exposes
Lambda Invoke through explicit arguments, not `--cli-input-json`; native invocation
and the provider fixture now use/verify that contract.

The v2 infrastructure rolled back on a missing RDS engine-default catalog read.
Native destroy reconciled the original delete token to `infrastructure_deleted`.
Independent original-stack resource reads plus ownership-tagged EC2/volume reads
proved absence before retiring its guard and identity stacks. No cluster/database
was created by that attempt. No uncertain resource mutation was blindly replayed.

Reviewed AWS public handler permission metadata, retaining only applicable reads:
engine defaults/versions, exact role attached-policy reads, bucket/repository
configuration reads and regional EC2 metadata. No service-linked-role creation,
identity writes or generic account-wide data/secret reads were added. Service-linked
roles remain a preflight prerequisite. A fresh v3 plan has new identities, fixed
expiry and journal, while preserving the old plan/binary for recovery.

All 55 CLI tests pass after the main handler fixes. The optional bridge's instance
credit metadata read was subsequently added to match EC2 instance observation.

V2 retirement is complete: main stack DELETE_COMPLETE, all recorded infrastructure
resources DELETE_COMPLETE, no tagged live instances/volumes, guard DELETE_COMPLETE,
bootstrap DELETE_COMPLETE by original ARN. The shared private guard-code publisher
is intentionally retained for v3. New bootstrap is CREATE_COMPLETE; guard creation
is in progress. V3 templates pass cfn-lint (intentional W3010 exception only), ten
AWS policies have zero Access Analyzer findings, Linux ARM64 CLI build passes,
and all 14 guard/chart tests pass. The service image is rebuilt from reused verified
runtime/server binaries plus the newly built CLI; no private trial input is included.

V3 independent guard reached CREATE_COMPLETE and passed the real native health gate.
The native coordinator then recorded/dispatched exactly one main-stack CreateStack
under the generated executor. Private networking and DB parameter/subnet resources
are progressing; EKS cluster creation is in progress, with no failed events observed
at the latest checkpoint. Fixed expiry: 29 October 2026, 22:10:58 UTC.

### Tagged EKS worker launch authorization

V3 reached actual EKS cluster creation, but the tagged launch-template node-group
request was rejected and CloudFormation began rollback. PostgreSQL creation was
cancelled; no agent was submitted. The AWS decoded authorization result identifies
`RunInstances` on the original deployment-tagged launch-template ARN (implicit deny),
matching AWS's documented caller requirements for tagged managed-node templates.

Added a separate Nodes permission domain rather than broadening the network policy:
owned launch template/subnets, original EKS-tagged cluster security group, fixed worker
instance type, new instance/volume ownership tags, and regional image/interface
permissions conditional on a separately ownership-authorized launch template. The
optional operator bridge's instance grant is separately limited to t4g.micro without
a launch template. No IAM write, generic EC2 launch or administrator role fallback.
New regression assertions cover dependencies and conditions. Prepare a fresh v4
bundle/identities while observing v3 rollback independently; keep original guard and
frozen binary until actual old resources are absent. Overlapping recovery is temporary
and charged to the same $100 experiment allowance, not a second budget.

V3 main infrastructure is now `infrastructure_deleted` through its original native
journal/delete token. V4 identity bootstrap is CREATE_COMPLETE and guard creation
has started under its generated setup role. V4 preflight passes every required check,
with eleven policy documents validated and no findings; estimate unchanged. All 55
CLI tests pass with the node-launch fix; Linux ARM64 release build and template lint
pass. The refreshed trial image includes the corrected CLI. No live model inference
or agent submission has been performed yet.

V4 guard completed and passed native health; its main stack is now creating through
the generated executor. Supplemental AWS IAM simulations pass eight allow/deny
cases across the attached policies: owned vs foreign launch template, tagged vs
untagged worker, fixed vs larger type, required template, standalone micro bridge,
and prevention of the bridge grant bypassing worker-template constraints. These
simulations are not a substitute for the pending real EKS node-group create.
All three reviewed local images are Linux ARM64 and below the publication size cap.
V3 main and guard are deleted with resource/tag absence verification; its final
identity-stack deletion has been submitted separately under the original operator.

V3 final identity stack is independently confirmed DELETE_COMPLETE by original ARN
and ownership tag. Both failed billable attempts are retired; only the current v4
trial and shared guard-code publisher remain. Private operator access is being
created again as part of v4, not reused from an absent/replaced instance.

### Actual EKS security-group context

V4's node create was also denied, this time on a security-group ARN with **no
ownership tags in the real authorization context**. The initial simulations covered
template/instance conditions, not that omitted launch-template dependency. The template did not specify security groups, and the denied group was not a
launch-template resource in AWS's decoded context. V4 is rolling back; retain its guard/frozen binary until retirement.

The generator now makes the node group use an explicit deployment-tagged worker
security group, with peer communication and private API/kubelet/webhook rules. API
control ENIs receive an additional owned management group; optional SSM access is
one further ingress from its own group. The private database accepts the actual worker
group. This avoids granting launch authority on untagged/default groups or altering
AWS's generated cluster group. Nodes depend explicitly on the required network rules.
Template and dependency-graph regressions pass; prepare a fresh v5 plan and verify the
actual SG dependency before waiting for EKS. This is still within the same trial budget.

V5 identity and independent guard stacks are CREATE_COMPLETE; real health passed
and native main creation was dispatched with the explicit worker-network template.
All 55 CLI tests pass again, as do the Linux ARM64 build and template lint. The
reviewed images are ready for native publication once infrastructure is complete.
V4 main and guard are deleted with independent inventory checks; identity deletion
is in progress. No model inference or agent submission has been run yet.

V5 actual-resource check passes: the AWS-created launch-template version contains
only the expected owned worker SG ID; that group belongs to the original VPC and
has the deployment ownership tag. AWS IAM allows RunInstances on that actual group
using the actual tags and launch-template ARN. The real node launch remains pending.
The new network ingress resources are CREATE_COMPLETE and PostgreSQL is creating in
parallel with EKS. V4 bootstrap is independently confirmed DELETE_COMPLETE by ARN/tag;
all earlier billable attempts are retired. Only v5 and the shared code publisher remain.

V5 PostgreSQL and the managed SecretTargetAttachment are CREATE_COMPLETE under the
scoped executor. EKS control-plane provisioning is still in progress. The live task
will write a fixed proof line and run a short sleep/read command so the UI can show
an active tool step; a direct read from the actual sandbox will independently check
the file effect rather than trusting model text or a user-prompt echo.

### Existing service-linked role read

V5 passed the EC2/template network authorization, then EKS rejected node creation
because the executor could not `iam:GetRole` the existing
`AWSServiceRoleForAmazonEKSNodegroup`. Preflight had verified existence using the
operator, but that did not grant the executor's own required read. Add GetRole and
ListAttachedRolePolicies on the exact four prerequisite service-linked role ARNs;
no identity creation/modification. Regression checks require four non-wildcard ARNs
and read-only actions. V5 is rolling back its actual DB plus cluster; preserve its
original journal/guard while preparing v6. Independently simulate those real role
ARNs under the new executor before starting infrastructure.

V5 rollback completed, including the actual private PostgreSQL instance, and native
original-token deletion reconciled to `infrastructure_deleted`. Independent inventory
checks precede guard/identity retirement. V6 template lint and regression pass. The
first v6 preflight hit transient read-request failures for pricing/code/policy reads;
the source AWS login remains valid. Refresh only the read-only observations into a
new report for the same immutable plan; no mutation acquired automatic retries.

The refreshed v6 read-only preflight passes all required checks; the previous blocked
report remains preserved. All 55 CLI tests and the Linux ARM64 build pass with exact
service-linked-role reads. V5 guard is deleted after actual resource absence; its
identity retirement is last. V6 bootstrap has been submitted under the original
operator. Estimate remains $7.67 fixed/24 hours plus $25 reserve, within the same
$100 authorization. No live agent has been submitted yet.

V6 bootstrap is independently CREATE_COMPLETE despite its CLI waiter losing a read
response; no bootstrap create was replayed. Guard creation was prevented locally
when preflight expired during intermittent AWS reads. A single automatic approval
review also timed out before process creation; its permitted retry reached that same
freshness gate, with no create sent. The private operator credential fixture now
caches scope-hashed temporary STS credentials in mode-0600 files (not exported to
pods/images/repo), avoiding repeated credential issuance for each read. Fresh v6
preflight passes all required checks again. Same immutable plan/journal and permission
scopes; no resource mutation has acquired retry behavior.

V6 per-resource IAM verification passes all 16 decisions against the four real
prerequisite role ARNs: eight reads allowed, eight identity create/delete cases
denied. Its guard is CREATE_COMPLETE, passes native health, and the original main
CreateStack has been dispatched. Only the current trial/code publisher remain;
all v1–v5 infrastructure/guards/identity attempts are absent. The private credential
cache improves connectivity without expanding scopes or changing mutation recovery.

V6 EKS control plane and actual managed worker node group are CREATE_COMPLETE. This
qualifies the corrected EC2 launch dependencies and exact service-linked-role reads
in the real create path. PostgreSQL, CNI, kube-proxy, CoreDNS and Pod Identity are
complete; EBS add-on is creating. No failed resource events. Scoped setup secret
handoff shape is verified (numeric port, expected DB/user/region, password present)
without printing or persisting its contents. Next is publication/native installation.

All v6 infrastructure is CREATE_COMPLETE and native phase is infrastructure_ready.
Private SSM + exact-host CONNECT bridge verifies original EKS TLS/CA and IAM setup
identity; both ARM64 AL2023 6.18 workers are Ready with no public IPs. The existing gp2
class is not default, so the owned encrypted EBS default can be installed safely.

The first real image publication stopped in Docker's auto-detected macOS Keychain
helper during login; journal remained prepared and no push intent was dispatched.
Terminated only that blocked helper. Publisher now seeds a non-secret registry entry
in a mode-0600 private Docker config, preventing automatic OS credential-store choice.
The fake Docker fixture requires that isolation; all 14 lifecycle tests pass. Patched
operator CLI inspects the unchanged original plan digest; original image/nonce/journal
are preserved for resumed publication. No old image or resource identity was adopted.

All three reviewed images published through native ows to exact owned repositories,
with immutable tags and verified config/manifest digests. Private auth/temp files are
removed on return; source image identity was preserved across the login fix.
Original-cluster handoff and hashed installation plan are prepared. Native platform
install is platform_ready; Agent Sandbox controller, encrypted tagged default EBS
class and trusted policies are installed, original CNI update reconciled to strict
mode without a second update. Actual packet probes pass baseline allow, egress
 deny/allow and ingress deny/allow; test resources are removed. OpenShell-only install
has started, with the pinned authenticated 0.1.3 chart/images.

The first gateway attempt failed in the cert-generation pre-install hook. The pinned
hook uses `openshell-certgen`/`certgen` labels, while the strict-CNI gateway policy
selected only `openshell`; its pod had already expired before logs could be read.
Added a separate exact-label hook policy for API TCP/443 and selected CoreDNS only.
Added `deploy cluster record-failed-release`: read-only remote verification of the
original terminal failed revision-1 release, nonce, namespace UID and receipt UID;
it records cleanup authority locally, never retries hooks or calls it ready.
All 14 lifecycle and 12 local-installer tests pass, including foreign nonce/revision,
replacement UID and cleanup-only checks. Original failed plan/journal retained;
partial uninstall preserves healthy AWS infrastructure and shared platform resources.

Original failed local installation is now destroyed with its recorded release UID;
AWS infrastructure and shared platform remain. Fresh installation has only three
new namespace policies (service, gateway, certgen); it never adopts shared objects.
Kubernetes omits empty NetworkPolicy rule arrays on readback. Added a narrow verifier
normalization for an omitted empty ingress/egress list only when the corresponding
explicit policyType remains; selector, allow rules and identity stay checked.
All 57 CLI tests pass. Original policy-create intent reconciles its exact existing
object without another create. Corrected platform is ready and gateway install runs.

Authenticated OpenShell is Ready; the service install exposed a second real-cloud
boundary: generated `sslmode=verify-full` is a libpq spelling rejected by rust-postgres
before connection. Fixed the generator to `require`; the service's mandatory Verified
native-TLS connector still validates CA and hostname using the RDS bundle. Added a
regression parsing the generated URL with the actual driver and reserved/Unicode
password characters. All 58 CLI tests pass.

For the existing trial only, performed an explicit operator repair of the owned DB
Secret with UID/resource-version/old-value preconditions (only that URL parameter,
no key/password/CA changes). Verified every failed bootstrap pod log consisted solely
of the pre-connect parser error before scheduling the same pinned bootstrap Job.
Repair intents and original UIDs retained privately. No Helm reinstall or unknown
model/tool action is automatically retried. The future fresh-install path emits the
correct URL directly; Secret staging continues refusing implicit content overwrites.

### Useful AWS milestone achieved (29 October UTC / 28 October Pacific)

Real agent `proof@1` on `aws@1` completed with Anthropic `claude-sonnet-5`:
three model requests, 3,767 input / 201 output tokens, two successful tools
(write_file and run_shell), streamed text deltas and terminal completed state.
Independent kubectl read of `/sandbox/aws-trial-proof.txt` matched the exact requested
line. Actual 1 GiB EBS workspace is Bound, encrypted, in-use and tagged to this cloud
deployment; volume read used the already authorized operator identity, without adding
volume-read permission to setup or weakening its policy.

CLI reconnect replay exactly matched all 33 run events and result. Rolled both idle
API replicas using UID/resource-version checks; restarted the local tunnel to the
new pod; replay again matched exactly, with no model/task resubmission. Browser ->
deployed UI pod -> API -> PostgreSQL qualification passed: 37 session trace steps,
result displayed, no page errors. A separate loopback convenience UI on port 58446
keeps verified TLS to AWS and an authenticated browser window is open for the user.
Demo admin credential expires 29 October 08:38 UTC and stays in a private file.

Independent guard configuration/rule/target/concurrency reads pass; health invocation
returns healthy, identityBound=true, destructiveActions=0. Fixed expiry remains
29 October 23:24:09 UTC (16:24 Pacific). Reviewed 24-hour estimate plus reserve is
$32.67 within the $100 incremental authorization; this is not a hard spending cap or
final actual bill. V1–V5 infrastructure/guards/identity attempts are absent. Keep the
healthy current trial briefly available for the user, then close sessions/delete
sandbox via service before native installer/cloud teardown; guard/bootstrap last.

Sanitized evidence: `evidence/aws-service-agent-20261029.json`. The trial succeeded
with the explicitly documented operator repairs. Do not call this a clean unattended
fresh-install qualification, S3 artifact transfer qualification or a healthy full
expiry test. Those remain follow-ups; this milestone proves AWS submission,
sandbox execution, progress/result presentation and durable API-node replacement.
