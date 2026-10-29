# AWS service qualification trial

28 October 2026. Branch `feat/openworker-service-m1`, starting at `9f78b8a2`.
User authorized the next AWS trial with the existing $100 experiment budget.

**Completed:** fresh clean install, real agent/UI/replay and actual scheduled expiry
all pass. Guard/bootstrap/code publisher are retired; independent resource inventory
is empty. [Sanitized evidence](evidence/aws-clean-expiry-20261029.json).
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

## Follow-up: clean install and scheduled expiry qualification

User authorized a clean-install rerun, full expiry cleanup, and pushing verified
changes, within the same cumulative $100 experiment budget. Preserve the successful
v6 evidence, close its session/delete sandbox, then remove the current deployment
using native teardown before creating a fresh short-lived trial. Freeze the final
installer and run it from fresh state against an empty EKS/RDS installation. No
manual Secret/Job/Helm repairs may count as a clean pass. Use the actual scheduled
expiry clock (no override or Lambda configuration edit), seed known owned data,
and verify actual cloud-stack, volume, object and identity absence. Retain the
independent guard until absence is confirmed. Add safe local reconciliation for
expiry-initiated deletion if needed; never invent a local delete request or replay
an unknown action. Update dated evidence, then push the feature branch after tests.

Budget gate: prior six attempts conservatively costed as six full 24-hour fixed
allocations is $46.02; a new one-hour fixed allocation is ~$0.33 plus a shared $25
reserve, giving a conservative $71.35 planning envelope. All old attempts ended
well before 24 hours. Billing attribution is delayed and the reserve is not a cap;
read actual account costs and resource inventory before proceeding.

Read-only follow-up inventory confirms only the v6 EKS cluster and its RDS instance
are present. AWS Cost Explorer reports ~$1.11 for October 28, estimated; October 29
is not yet attributed. V6 completed session is closed and OpenShell environment is
now deleted through the service, including its workspace PVC. Native service teardown
is running; successful v6 evidence remains saved.

Added a read-only expiry reconciliation path: after the fixed deadline it verifies
the original guard stack/config/code, strongly consistent deployment-keyed control
record, original infrastructure ARN/template/creation token plus stack-level expiry
delete token, owned-bucket absence and complete empty tagged-volume inventory. Only
then records the observed external delete locally. It never issues DeleteStack or
requires an API pod that expiry has already removed. The setup role gains only
GetItem on its one control key and regional EC2 volume metadata reads, no control
writes/deletes. Tests cover wrong identity/version/clock and no pre-expiry AWS calls.

All 61 CLI tests pass, including expiry identity/clock/read-scope checks. Final Linux
ARM64 runtime image builds from source and its packaged `reconcile-expiry` command
passes a smoke check. Old v6 service/gateway native teardown is destroyed; main
stack is deleting under its original recorded request, with RDS and SSM bridge
already deleted and no failed events. Old local UI/access listeners are retired.

Clean-trial image is built directly from final source (not the cached prebuilt
service binaries). It includes the new read-only expiry observer. The forthcoming
one-hour plan uses the same preflight-verified version catalog and exact published
guard bytes, with a new deployment identity, empty database and fresh platform/Helm
state. The old long-lived trial is being completely retired before its clock starts.

V6 independent inventory passes: no cluster, active instances, volumes, VPC, EIPs,
RDS, repositories, owned bucket or trial roles. Guard and bootstrap are DELETE_COMPLETE
by original ARN. Fresh v7 one-hour deployment is `ows-32a76230ebd74e71afd9ab599e774dea`,
fixed expiry 29 October 04:26:57 UTC / 28 October 21:26:57 Pacific. Fresh read-only
preflight passes all required checks (54 AWS calls, eleven policies with zero Access
Analyzer findings). Published fixed one-hour estimate is $0.32 plus $25 reserve.
Customer bootstrap has been dispatched once. Linux image offline export independently
shows the final observer read scopes. No manual installation repair is permitted
for this clean qualification.

Fresh plan bootstrap is progressing with no failed resources. The expiry observer
uses only read APIs (CloudFormation events, consistent control-record read, tagged
volume/bucket inventories and the exact guard log group); it never invokes cleanup
or edits the expiry clock. It will distinguish an incomplete upload and object
cleanup from just accepting a stack-delete response. The existing publisher bucket
is retained only while the new guard still needs its reviewed code version.

V7 independent guard is CREATE_COMPLETE and passed native code/config/schedule/
health validation. Original infrastructure create was dispatched once and is
progressing without failed events. CLI expiry-recovery implementation is frozen in
commit `35cab713`; dated live evidence will follow separately. Independent CloudTrail
verification will require the cleanup role as DeleteStack actor, the original ARN,
the stable expiry token and a request time after the actual fixed deadline.

V7 real IAM simulation passes five observer checks: own deployment control-key read
allowed; foreign-key read, control write, table scan and EC2 volume deletion denied.
This is simulation evidence, not a substitute for the later real GetItem/volume
reads. New EKS cluster, managed nodes, RDS and core add-ons are CREATE_COMPLETE;
EBS add-on is finishing, with no failed infrastructure events.

Fresh v7 infrastructure is CREATE_COMPLETE and native phase infrastructure_ready.
Two managed nodes, all five add-ons, empty RDS and private SSM bridge are ready.
Pinned source-built harness image is publishing through native ows; exact private
endpoint/CA access is established. The clean test will use new installer state and
new Secrets rather than carrying the earlier operator repairs forward.

Fresh platform is platform_ready: all sixteen namespace/resource operations complete,
zero explicit no-effect resets, original CNI update observed without a second update.
Functional traffic probes are running in the owned service namespace. Authenticated
OpenShell gateway installs separately using the exported certgen policy; no Secret,
Job or Helm repair has occurred.

All five real strict-network probes pass again; their test resources are removed.
Fresh authenticated gateway is deployed/Ready on its first Helm revision, with no
qualified-after-timeout flag, failed-release receipt or recovery reset. This clean
path uses the exported narrow certgen policy directly. Fresh Secrets are staging
through the native installer; no manual Secret or Job edits have occurred.

Clean install passes: both gateway and service Helm revision 1 are deployed; every
installer operation is complete, confirmedNoEffect is empty, no failed-release or
qualifiedAfterTimeout receipt exists, and there are no operator repair files.
The original bootstrap Job completed in 12 seconds. All API, worker, controller,
platform and UI processes are Ready. This fresh empty-RDS path directly exercises
the corrected URL generator. Seeded an owned S3 object (SHA-256 readback matches)
and one unfinished multipart upload; this is an operator S3 round-trip, not a claim
that the workload SDK's S3 transfer path is qualified. Agent configuration succeeded
and a new live model submission is underway.

Fresh live agent completed successfully: three real model requests and successful
write_file/run_shell calls. Independent cat read confirms the exact file on the
new encrypted, ownership-tagged EBS workspace. UI trace/result and live connection
pass with a timeout covering the idle heartbeat. A five-second indicator assertion
initially failed because the temporary TLS presentation proxy (not the shipped UI)
omitted flushHeaders; corrected that private test fixture and rechecking it.
No production image, Helm release, Secret or Job is being repaired or replaced.

Pre-expiry observation at 04:14 UTC: main CREATE_COMPLETE, deleteIntent=null,
no expiry delete token, one owned S3 object and one multipart upload, five encrypted
in-use volumes, and scheduled not_due reports with zero destructive actions. The
known workspace and gateway disks remain for the guard's orphan-volume path.

UI recheck passes with the original strict five-second assertion after fixing only
the temporary TLS fixture: live connection, 40 session trace steps, actual result,
zero page errors. The shipped UI already called flushHeaders; no product code or
cloud deployment changed. Completed run replay exactly matches all 36 run events
and result; no model/tool task was resubmitted. All clean-install gates remain true.

Pre-expiry reconciliation gate rejects before any provider call and leaves the local
journal unchanged. Last pre-deadline snapshot still has all five encrypted volumes,
one object and one upload, control version 1/deleteIntent=null, no failed resources
and no expiry delete token. Guard logs are paged explicitly to avoid mistaking a
partially returned CloudWatch page for complete evidence. The fixed deadline is
now due; only read-only observations will follow until the scheduled guard finishes.

The real deadline has passed. No manual infrastructure-delete request was sent for
v7 and no clock, schedule, Lambda environment or guard control record was edited.
Only read-only observers run while the actual five-minute schedule performs cleanup.
Local listener PIDs are recorded for precise test-process retirement afterward.

Scheduled guard automatically requested deletion at 04:28:05 UTC, 68 seconds after
the fixed deadline. Main stack is DELETE_IN_PROGRESS with the stable original
`<deploymentId>-expiry` token; control version 2 records that same intent. Owned
S3 bucket is already absent, including the seeded object/upload. SSM bridge/root
disk are gone; four encrypted in-use worker/workspace volumes remain temporarily.
No failed CloudFormation resources or guard errors, complete paged logs. No manual
v7 DeleteStack request was made. Next observations must prove orphan disks and all
infrastructure absent before retiring the guard/identities.

The automatic expiry delete token is confirmed in original stack-level events.
CloudTrail actor lookup is initially empty (delivery lag); retain it as pending,
not proof of actor absence. Real read-only native reconciliation is being checked
while deletion is in progress: it must read its newly scoped guard/control receipts
successfully but refuse to mark pending deletion complete or write the journal.

Real post-expiry native reconciliation passes its guard identity/config/health and
strongly consistent control read using the new setup role, then correctly rejects
DELETE_IN_PROGRESS. Journal bytes remain unchanged and no remote mutation occurs.
This qualifies both the new read scope and the pending-deletion safety gate against
actual AWS, before the forthcoming positive completion path.

CloudTrail now confirms one successful DeleteStack against the original main ARN,
issued by the exact generated cleanup role at 04:28:06 UTC after expiry. CloudTrail
omits clientRequestToken from this event; the stable expiry token is independently
verified from original CloudFormation stack events and the consistent control record.
At 04:41 UTC the original main stack is DELETE_COMPLETE, artifact bucket absent,
all instances/root volumes removed. Two detached encrypted 1 GiB workspace/gateway
volumes remain awaiting the next real scheduled guard pass. No manual volume delete
or main-stack delete is issued. All nine expiry guard regression tests pass as well
as the 61 native CLI tests.

At 04:43 UTC the next unchanged five-minute guard schedule deleted both detached
owned volumes. Control version 4 records each exact volume intent; independent
EC2 inventory is empty. Bucket and main stack are absent. Native reconcile-expiry
now succeeds against real AWS and a repeat succeeds too, with remoteMutation=false
in both results. Original-ARN/token/config checks remain enabled. Waiting for the
next scheduled empty-inventory completion observation before guard retirement.

Billing refresh at 04:44 UTC reports $10.3215683861 for UTC 28 October and $0 so
far for 29 October, both estimated. This is account-day billing with delayed
attribution, not a final per-deployment bill. No EKS clusters or RDS instances remain.
The deliberately conservative cumulative planning envelope remains $71.35 within
the unchanged $100 authorization. Exact recorded local trial listeners were stopped;
no unrelated processes, Docker resources or local Kubernetes workloads were changed.

At 04:48:06 UTC the unchanged schedule emitted teardown_observed_complete with
zero pending disks and zero new destructive actions. Complete paged guard logs
contain no errors. Main DELETE_COMPLETE, original expiry token/control identity,
bucket absence and empty owned volume inventory are independently confirmed.
Retiring original guard, then bootstrap identities, then the now-unused shared code
publisher; these final retirements are explicit operator actions after workload
absence, not claims that the guard deletes its own control plane.

Final retirement passed: original guard and bootstrap both DELETE_COMPLETE.
Independent inventory at 04:50:53 UTC found zero original clusters, active instances,
volumes, VPCs, EIPs, databases, repositories, roles, functions, control tables,
schedules, log groups, managed policies, database Secrets or artifact buckets.
The shared publisher's exact code version was deleted, its original stack retired,
and its retained empty bucket deleted last; all three absence checks pass.
Task-private credential/token/key copies and credential caches were removed after
evidence capture; original AWS and model credentials remain untouched. No unrelated
account resources or local baseline workloads were modified.

Final checks: 61 CLI tests, 9 expiry guard tests, full Linux ARM64 source image build,
network-disabled image CLI smoke/export, real IAM scope simulation and real
pre-expiry/pending/completed/repeated reconciliation gates passed. Sanitized evidence
and docs updated; publication is to public feature branch feat/openworker-service-m1,
with no pull request or main-branch merge. S3 workload SDK/Pod Identity transfer and
final delayed billing attribution remain follow-ups, not claimed as qualified.

## Artifact storage follow-up — 29 October 2026

User authorized the next artifact-storage qualification under the same cumulative
$100 budget. Keep existing resources retired and do not touch unrelated workloads.

1. Inspect the existing immutable S3 adapter and artifact HTTP/catalog boundaries.
   Exercise Pod Identity token-file authentication, credential refresh, missing or
   denied credentials and absence of instance/operator credential fallback locally.
2. Use isolated Docker PostgreSQL/S3 fixtures to verify upload, checksum, interrupted
   transfer recovery, cross-API download and retrieval after API replacement. Reuse
   cached images; remove only this qualification's containers and state afterward.
3. Refresh AWS spend and inventory before provisioning. At 12:19 UTC, account-day
   estimated charges total $15.58 across 28–29 October; EKS and RDS inventories are
   empty. Billing is delayed. Prior conservative planning envelope is $71.35;
   review any new fixed cost and reuse the existing shared variable reserve.
4. Run the smallest bounded real EKS Pod Identity trial: the shipped adapter/API
   must upload/read its own prefix with no static AWS credentials, survive process
   replacement, and explicitly deny a foreign prefix/bucket and unassociated pod.
   Use original identities and durable mutation receipts, fixed expiry and scoped
   teardown. Never retry an uncertain tool/storage mutation blindly.
5. Record sanitized local/cloud evidence and remaining limits, retire all owned
   infrastructure and copied credentials, update docs and push the feature branch.

Initial inspection found that an incomplete Pod Identity environment could fall
through to instance metadata in the dependency credential chain. Require both
the container endpoint and authorization-token file when either is configured;
verify this fails before any network call. No change to explicit injected providers.

Local artifact follow-up passes: seven ordinary storage/credential tests, including
projected-token refresh, denied/missing token and either incomplete-pair direction;
three real Docker-backed suites (filesystem API restart, immutable S3 streaming,
S3 API restart). HTTP tests use a fixture completed turn, not model/tool execution.
They prove truncated-upload pending status, exact-ID retry, checksum, lost-ready
reconciliation, authenticated cross-API download, both original APIs replaced, and
no duplicate ready events. Owned Docker fixtures are removed. Initial local test
runner/ELF-path corrections affected only fixtures, not deployed service behavior.

AWS gate: use one additional one-hour private EKS/RDS reference trial, deterministic
agent only, cached reviewed images, no paid model calls. Published fixed estimate
will be rechecked by native preflight. The prior conservative $71.35 envelope plus
one more $0.33 fixed-hour allowance is $71.68, retaining the shared $25 variable
reserve rather than treating reported delayed billing as settled cost. Guard-code
publisher will be fresh; original retired stacks/journals are never reused.

Fresh AWS preflight passes all 54 provider calls. Published one-hour trial planning
estimate is $25.32 including its $25 variable reserve; cumulative conservative
envelope remains $71.68 using the same shared reserve. Deployment
`ows-d32f5ba42cd8402da0585901909bcb19`, plan digest
`eccb73bd4c16ec96979f961183fe3c7cf25c1480aadb2fbb5c3927afb45fec1a`,
fixed deadline 29 October 13:36:36 UTC. Fresh publisher is version-pinned; original
trial remains retired. Generated bootstrap identities now submitted once. No model
key is read: the installed producer agent will use the deterministic model fixture.

Managed service inspection exposed a real integration gap before image publication:
legacy artifact routes were hidden with compatibility disabled and had no /v1
equivalent. Added /v1 routes using the existing storage/catalog handlers, explicit
read versus artifact-write authority, namespace checks, transfer limits and capability
advertisement. Fresh real Docker qualification passes all three suites with /v1,
legacy=false, 404 on old routes and 403 on wrong namespace. Seven server unit tests
pass. Rebuilding the source image before publication; no deployed-image repair.

Managed /v1 routes and all fresh local suites pass with namespace/authority
enforcement and compatibility disabled. Source service image rebuilt before cloud
publication. Both associated-role and unassociated-pod probe binaries compile on
Linux ARM64; the unassociated negative-control logic passes an offline Docker
smoke with networking disabled. Cloud infrastructure remains CREATE_IN_PROGRESS
with no failed events; private operator bridge is ready, EKS/RDS are still creating.
No provisioning requests are repeated while waiting.

At 12:54 UTC RDS and the operator bridge are CREATE_COMPLETE; EKS control plane
is still creating, with no failed infrastructure events. A bounded read-only waiter
observes the original stack ARN while provisioning completes; create is never replayed.
The test-only unassociated mode verifies missing Pod Identity inputs plus failure to
obtain fallback metadata credentials, and its offline smoke passes. Associated API
and worker checks require STS identity equal to the generated artifacts role and
real AccessDenied for both reads and conditional writes outside the owned scope.

At 12:59 UTC original infrastructure reached CREATE_COMPLETE: private EKS 1.36.5,
two managed ARM64 workers, RDS 16.13 and all five add-ons. Native coordinator now
records infrastructure_ready. Reviewed source-built service and unchanged runner
are immutably published to the new exact ECR repositories; SSM route reaches both
original nodes without changing endpoint/CA or public access. Native installation
handoff is prepared with scripted=true and no model-key read or model Secret.

Native installer plan is bound to original cluster and namespace identities:
`ows-local-caa24c638bab4071ac80eaa5afe8ffbf`, digest
`3f10d5f87a004f29a89a4d80c9fd15a611c42aeb6a0e65b27df46507609bfff2`.
The first direct planning invocation lacked the task-private AWS profile environment
and failed during read checks before creating installer state or mutation intent.
Running that read-only planning step through the environment wrapper passed; no
cloud mutation was retried or repaired. Platform installation now starts normally.

Platform reached platform_ready after observing its original CNI update; no second
update was sent. All five real packet allow/deny probes pass and exact probe resources
are removed. Authenticated pinned OpenShell gateway is openshell_ready on its first
installation, with no no-effect resets or operator repairs. Generated TLS/RDS/bootstrap
Secrets are now staged through the native installer, with no model Secret or key.

Real S3 producer upload and cross-API SHA-256 download succeed. The first larger
truncated transfer is rejected by the deployed TLS proxy with 413 before reaching
the adapter: the proxy's 160 KiB JSON limit also applied to artifact bytes. Source
fix adds a content-route-only 64 MiB streaming limit and 120-second bounds, disables
request buffering, and keeps other routes at 160 KiB. All six Helm boundary tests
and real nginx syntax validation pass.

A reviewed chart package is being applied as an explicit one-shot Helm upgrade to
revision 2, with original namespace/release/value/installer receipts captured and
package SHA bound before mutation. No ConfigMap patch or installer-journal rewrite.
This run will qualify the source fix through an upgrade, not claim a clean initial
AWS install with the final proxy. Same artifact IDs/bytes will be reconciled; the
producer tool is never rerun. Because native teardown correctly refuses changed
release revisions, retain the original independent expiry guard and reconcile its
external scheduled teardown afterward; do not forge a new installer receipt.

Real /v1 S3 storage qualification passes after the reviewed proxy upgrade. The
36-byte producer file and 2 MiB+73-byte binary download with matching SHA-256
through both API pods. Post-fix truncated upload returns 409/stays pending; exact-ID
retry and read-only reconciliation complete without duplicate ready events or
producer/tool replay. Independent S3 HEAD checks verify original bucket ownership
and SSE-S3 encryption on manifests and parts; operator performed reads only.

Actual STS probes in both API and worker identify the generated artifact role,
with static keys absent. Foreign-prefix and foreign-bucket GET and conditional
PUT requests return permission-denied, not timeout/missing-object success. A fresh
unassociated/default-SA non-host-network pod receives no Pod Identity environment
and cannot obtain fallback node metadata credentials.

A separately recorded UID/RV-preconditioned API rollout replaces both processes.
Both artifacts remain hash-exact through both replacements, with two ready events
and exactly the original two tool calls. Producer session is closed, original
environment is deleted with zero attachments, exact sandbox pod is absent, and
both artifacts remain retrievable. Keeping the fixed original expiry schedule for
final infrastructure/S3/orphan-disk cleanup; no deadline or guard control edits.
