# OpenWorker Service: connection, deployment and control plane

Status: design proposal for implementation review · 27 October 2026

This specifies the next service interface. New SDK methods, CLI commands and
`/v1` routes below are proposed, not a claim that the current server reference
implements them. Existing local SDK/stdio APIs remain supported.

For a visual review of the deployment shapes and minimal SDK examples, open
[the architecture companion](SERVICE-ARCHITECTURE.html).

## 1. Decisions and boundaries

Use **OpenWorker Service** as the product name and **`ows`** as the service CLI.
Reserve `ow-manage` as a possible packaging alias, not a second command surface.
`ows` manages and submits work to a service; it does not replace the embedded
Rust SDK or the desktop app. Check executable/package-name availability before
publishing a distribution.

The application owns users, projects, product UI and product authorization. The
service owns admitted agent definitions, sessions, runs, execution bindings,
approvals, durable events and operational controls. Kubernetes/cloud operators
own infrastructure installation and infrastructure credentials.

External clients use authenticated HTTPS REST and SSE. A separately deployed
management UI uses the same API. There is no public database, worker-management
port, ToolRunner endpoint, Kubernetes API proxy or arbitrary remote shell.
Outbound model calls and authenticated private execution-provider channels are
still required; “REST-only public ingress” does not mean all internal traffic is
REST or that the cluster has no outbound connections.

```text
Product backend ─ SDK ─┐
ows CLI ───────────────┼── HTTPS REST / SSE ─── Harness API replicas
Management UI ────────┘                              │
                                           PostgreSQL commands,
                                           state, leases, events
                                               │         │
                                      Agent workers   Management controller
                                           │                 │
                                 Execution providers    Narrow platform adapter
                                           │                 │
                                  ToolRunner + files    Worker replicas/config
                                  local or remote       on Docker/Kubernetes
```

The three placements are independent deployment choices:

1. App backend starts a harness subprocess with `Harness.start()`.
2. Backend uses `Harness.connect()` to a service; tools execute on service-local
   execution capacity provisioned under an explicit policy.
3. Backend connects to a service whose tools execute in another environment,
   potentially across a VPC boundary through a provider adapter.

A session owns durable conversational state, not a permanent worker process.
A run is one admitted task/turn. An environment owns workspace files and runtime
processes. A worker lease binds compatible compute to session execution.

## 2. Existing foundation and actual gaps

Reuse, rather than replace:

- [Server reference](../../harness/server-reference/README.md): REST/SSE APIs,
  PostgreSQL command admission, worker leases and a separate priority control path.
- [Authorization](../../harness/server-reference/AUTHORIZATION.md): scoped bearer
  credentials, revocation, admission audit and event-stream authorization.
- [Environment provider contract](../../harness/runtime/src/environment.rs):
  explicit create/inspect/attach/delete, stable bindings and uncertain outcomes.
- [Helm deployment](../../deploy/helm/README.md),
  [local operations reference](../../deploy/reference/README.md),
  [AWS qualification](../../deploy/aws/README.md), and the existing read-only console.
- Runtime/tool policy, plan/team/session state, compaction and telemetry contracts.

M1–M4 now provide the remote SDK transport, deployed releases, RuntimeProfiles,
OpenShell provisioning, combined task admission, expiring service tokens,
revisioned settings, Kubernetes pool management and `ows`. The
[local service package](../../deploy/service/README.md) runs the API, controller,
worker and PostgreSQL in Compose, connecting to an explicitly provisioned
Kubernetes/OpenShell gateway. It is not an all-in-one Docker-only execution
provider. See the [progress ledger](SERVICE-PROGRESS.md) for qualification evidence.

Remaining gaps include broader namespace management, browser SSO (deferred by the
user), REST-managed model-provider/secret enrollment, live worker-slot updates, a
Docker-only execution provider and a writable management UI. M4 settings cover
admission controls and pinned compaction defaults; model connections/keys and
worker slots remain installation configuration with controlled rollouts. Cloud
qualification follows when AWS/GCP clusters are provided.

Existing versioned worker pools remain the server upgrade model. The local SDK
currently pins the runner binary per session; changing it requires a new session.
This proposal does not silently relax either identity check.

## 3. Application-facing SDK

### Connect versus own a process

```typescript
// Existing local mode: app owns and supervises its child process.
const harness = await Harness.start({ stateDir: './agent-state' });

// Proposed service mode: app connects; it does not launch/restart cluster workers.
const service = await Harness.connect({
  url: process.env.OPENWORKER_URL,
  namespace: 'course-platform',
  credentials: { tokenEnv: 'OPENWORKER_TOKEN' },
});

const coder = await service.agent('coder@v1');
const session = await coder.newSession({
  idempotencyKey: 'workspace-42-session',
  runtimeProfile: 'standard', // Optional: otherwise use the authorized default.
  // Optional; must be allowed by the deployed agent and provider grants.
  model: { provider: 'primary', name: modelId },
});
const run = await session.send('Implement the requested feature.', {
  idempotencyKey: 'feature-456',
});
for await (const event of run.events()) render(event);
const result = await run.result();
```

Rust gets the same conceptual connection/session/run interface with idiomatic
async builders. Both SDKs share host-side agent behavior; REST transport must not
reimplement the loop, teams, compaction, permissions or recovery in the client.

`connect()` authenticates and reads `/v1/capabilities` plus caller identity. It
validates API compatibility and supported features before returning. TLS is
required except explicit loopback development; custom CA trust is supported
without disabling certificate/hostname verification. Remote options reject local
executable/state paths instead of silently ignoring them.

Credentials may come from an environment variable, an explicit token or an async
access-token callback. Client tokens authenticate to **OpenWorker Service**, not
to an LLM provider. Tokens are redacted and never put in URLs or CLI flags. A 401
can trigger one credential refresh; mutations retain their original idempotency
key. Persistent rejection is returned, not retried indefinitely.

### Agent definitions and deployment are different from execution

`createAgent()` keeps its existing local meaning. It must not implicitly publish
an unreviewed definition to a production cluster. In service mode:

- `service.agent('coder@v1')` resolves an authorized active release and returns an
  agent handle. An alias resolves once to an immutable release digest.
- A publisher deploys a declarative bundle through management APIs/`ows agent
  deploy`; a release administrator activates it. A development policy may grant
  both capabilities to the same principal.
- Definitions describe instructions, tools, skills, model defaults, compaction
  defaults and permitted RuntimeProfiles. Teams use the same release model.
- Tools must be registered in compatible trusted worker/runner images or explicit
  MCP integrations. A JavaScript function in the caller cannot be shipped as JSON
  and executed remotely. Remote sessions reject local application callbacks.
- Custom native tools require a reviewed immutable image plus matching registry;
  deploying a bundle does not grant permission to deploy arbitrary native code.

Session/send/state/result/event shapes should remain consistent across local and
remote modes. Session references include service identity, namespace, release
and session IDs, never credentials. Resuming validates that identity rather than
accidentally opening an equally named session on another service.

A connected client's `disconnect()` only closes client resources. It never stops
service workers or cancels runs. Local process shutdown retains its existing
meaning. API capability flags describe genuine backend differences.

## 4. RuntimeProfiles and reusable execution environments

A **RuntimeProfile** is the application-facing choice of where and under what
constraints an agent executes. An execution environment is the actual sandbox
allocated internally from that profile. Ordinary applications need not provision
an environment separately before starting a session.

Infrastructure hosting and execution placement are independent. For example,
OpenWorker Service can run on EKS with an OpenShell gateway in that cluster,
while another registered provider supplies remote execution. Start with OpenShell;
other provider adapters require separate implementation and qualification.

Administrators register provider instances (gateway/API identity plus secret
references) and immutable RuntimeProfile revisions. Example proposed configuration:

```yaml
defaultRuntimeProfile: standard
runtimeProfiles:
  standard:
    provider: openshell-local
    image: registry.example/runner@sha256:<approved-digest>
    cpu: 2
    memoryMB: 4096
    permissions: { filesystem: workspace-only, network: approved-destinations }
    sharing: exclusive
    persistence: project
  team-workspace:
    provider: openshell-local
    image: registry.example/runner@sha256:<approved-digest>
    cpu: 4
    memoryMB: 8192
    permissions: { filesystem: workspace-only, network: approved-destinations }
    sharing: shared
    persistence: project
```

The permissions values illustrate policy references, not a replacement for the
existing resource-policy schema. Profiles also define bounded lifetime, tool
credential references and allowed resource overrides. `memoryMB` means execution
RAM; conversational memory/compaction is configured separately. Agents must have
permission to use a profile; possessing its name does not grant access.

An agent release can select a default `runtimeProfile`. Session creation can
specify an authorized override. Resolve in this order: explicit session choice,
agent default, namespace default, service default. Pin the resolved immutable
profile revision and provider binding in the session/environment record. If no
valid default exists, return a configuration error, not worker-local execution.

```typescript
const coder = await service.agent('coder@v1');
const session = await coder.newSession(); // Uses the resolved RuntimeProfile.

const alternate = await coder.newSession({ runtimeProfile: 'burst' });
// A separately configured/authorized remote profile; no automatic migration.
```

Multiple profiles may select different providers. Bursting initially means placing
new environments on an explicitly chosen profile. Automatic placement is a later
administrator-defined routing policy with cost/capacity constraints, permitted
providers and no silent weakening of security. Neither selection nor scale-up
moves a running workspace/process between providers.

### Reusing an execution environment across agents

Return a service-generated **`environmentId`** with each session creation receipt,
and expose it as `session.environmentId`. The ID maps to a versioned provider binding in the service; each admitted
binding revision pins verified execution and storage identities. It is sufficient to request another attachment without
requiring the caller to know pod names, remote sandbox IDs or gateway credentials.
Authorization and readiness checks still apply.

```typescript
const coder = await service.agent('coder@v1');
const reviewer = await service.agent('reviewer@v1');

const codingSession = await coder.newSession({
  runtimeProfile: 'team-workspace',
  idempotencyKey: 'project-42-coder',
});
await app.projects.saveEnvironment('project-42', codingSession.environmentId);

const reviewSession = await reviewer.newSession({
  environmentId: codingSession.environmentId,
  idempotencyKey: 'project-42-reviewer',
});
```

`environmentId` and `runtimeProfile` are mutually exclusive session inputs.
Attaching uses the environment's existing profile; it cannot change its provider,
permissions, image or memory allocation. Both agents must be compatible with
that profile and worker/runner cohort. The service checks namespace/project
ownership, caller grants and sharing policy. Same-user ownership permits a useful
default scope but is not a bypass for cross-project or cross-namespace access.
An environment ID is a lookup key, never an access token.

Two attached sessions have independent conversations, plans, approvals and private
session memory. They share workspace files and the environment's filesystem/network
authority. Runner attachments and process handles remain session-scoped unless an
explicit shared-process API is introduced. Do not assume an agent can control
another session's process by reusing its handle. Shared filesystem access is not
file-write coordination: use the existing hashes/edit conflict checks, task
coordination, or separate working directories. Concurrent servers can conflict on
ports within one environment. Untrusted tenants must not share an environment.

### Shared-workspace use cases

Primary use cases are a coding agent and verifier in one Agent Team, or several
agents belonging to one user/project working in separate directories or Git
worktrees on the same execution machine. The verifier may inspect the coder's
output in place or a named revision/check-out. The application/team coordinates
when an artifact is ready for verification; sharing alone does not make concurrent
file reads a coherent snapshot.

Proposed session option `workingDirectory` is a workspace-relative directory:

```typescript
const coding = await coder.newSession({
  runtimeProfile: 'team-workspace',
  workingDirectory: 'worktrees/coder',
});
const verification = await verifier.newSession({
  environmentId: coding.environmentId,
  workingDirectory: 'worktrees/verifier',
});
```

The directory must exist before use (or be created through a separately authorized
workspace setup operation). Validate traversal, symlinks and allowed mount roots.
Do not silently create Git branches/worktrees as a side effect of attachment.
A working directory is a default execution location, not a security boundary;
the profile and actual sandbox enforcement define accessible files. Worktrees
share repository metadata and may require Git coordination. A verifier's narrower
tool permissions remain separate from the shared machine's OS authority.
All sessions consume the same environment's aggregate CPU/RAM capacity; track
attachment limits and resource pressure. Worktree separation does not separate
network ports. Tests must cover two agents attaching/detaching independently,
conflicting writes/ports, and verifier reads of a selected committed revision.

Default profiles are exclusive. A shared profile explicitly allows multiple
concurrent attachments; switching an existing exclusive environment to shared is
not an implicit side effect of starting another agent. Closing/cancelling one
session releases only its attachment and owned processes, not other sessions'
attachments or the shared environment. Last-attachment release follows a declared
retention policy; data deletion remains a separate authorized operation.

Expose authorized environment metadata through `service.environments.get(id)`:
provider name, resolved RuntimeProfile revision, lifecycle state, ownership,
sharing mode and active attachments. Provider credentials are never returned.
A reusable stored reference includes service identity, namespace and environment
ID so it cannot accidentally attach to an unrelated service. Deleted, incompatible
or unknown environments return explicit errors, never a fresh workspace under the
same ID. Reusing an environment does not waive session/runner upgrade restrictions.

### Provider-specific execution and storage identities

Expose the stable OpenWorker `environmentId` to applications. Do not expose a
provider's sandbox ID as though it were a universal environment identifier.
An environment can comprise several provider resources with different lifetimes.
Store their verified identities together in a provider-owned binding payload.
Illustrative logical schema, not literal Kubernetes/Modal API fields:

```json
{
  "environmentId": "env_project_42",
  "bindingRevision": 1,
  "provider": "openshell-local",
  "runtimeProfileRevision": "team-workspace@1",
  "execution": {"providerResourceId": "sandbox-or-pod-id", "generation": "generation-1"},
  "storage": [
    {"role": "workspace", "providerResourceId": "workspace-or-volume-id",
     "mountPath": "/sandbox", "persistence": "retained", "management": "service"}
  ]
}
```

The provider adapter validates this schema/version and supplies the exact identity
fields needed by its backend; keep opaque provider details out of generic client
logic. No credentials are embedded in the binding. Each resource records ownership
and lifecycle independently (service-managed versus application-managed).

- **OpenShell first:** retain the gateway sandbox identity, its verified persistent
  workspace scope, accepted policy and image identity. Obtain storage identity
  through the supported gateway/provider contract. Do not invent an OpenShell
  volume ID or bypass the gateway by inspecting arbitrary Kubernetes objects.
- **Native Kubernetes adapter, later:** record cluster/namespace identity and pod
  UID, plus persistent claim UID and appropriate backing-volume identity. Names
  alone are insufficient because a deleted name can be reused. Record mount/access
  mode, storage class and retention policy as needed to verify safe reattachment.
- **A provider such as Modal, later:** if compute and persistent storage are
  separate resources, retain both the sandbox reference and every attached volume
  reference, mount point and ownership. Qualify its actual mount, consistency and
  lifecycle semantics before claiming compatibility; do not assume volume sharing
  or data flush behavior from an ID alone.

Compute loss and workspace loss are different states. Stopping or replacing a
sandbox must not silently delete its retained workspace. Conversely, a running
sandbox with a missing/replaced volume is not the original environment. Reject
reattachment on identity mismatch; never substitute an empty disk under the same
environment ID. Process handles do not survive compute replacement.

Recreating compute while retaining the environment ID requires an explicit,
idempotent recovery operation: verify retained storage, compatible image/profile
and permissions, fence/reconcile the old execution instance, then atomically admit
a new binding revision and attachment generation. Stale workers cannot act against
the new generation. Providers that cannot prove these conditions report recovery
unsupported or unknown. This is not automatic live migration and does not waive
existing session/runner compatibility requirements. Workspace deletion requires
its own authorization/retention decision; external volumes are never deleted
merely because the service released its attachment.

### Provisioning, registration and attachment mechanics

Profile-based session creation durably allocates session/environment IDs and a
provisioning operation before contacting the provider. It can return `202` with
those IDs; the SDK waits for readiness before returning a usable session. The
receipt remains inspectable if that wait is cancelled or the connection is lost.
Cancelling a wait does not delete the environment or cancel provisioning. Repeating
the same idempotency key retrieves the original operation.

Workers attach only after the environment is ready and its image, workspace,
policy and ownership have been verified. Provider create/attach operations use
stable internal operation IDs. An unknown result is reconciled against the
original remote identity; it is never replaced with another environment.

Advanced callers may explicitly create an environment from a RuntimeProfile or
register an existing application-owned sandbox through management APIs. Registration
inspects and verifies the remote object against an approved profile before issuing
an environment ID. The service does not own deletion of application-managed
objects. Neither registration nor an arbitrary remote ID proves authorization.

LLM credentials stay on workers. ToolRunner receives only credentials scoped to
its permitted tools/resources. External providers may use authenticated outbound
connections or a verified private gateway; no generic inbound public ToolRunner
port is required. Provider-specific connectivity and identity must be qualified.

### Co-located versus separate execution placement

RuntimeProfiles name an approved placement policy as well as a provider. The
architecture examples use `openshell-colocated` and `openshell-remote` as example
profile names, not built-in reserved names or already implemented capabilities.
Co-located means a harness worker and its sandbox occupy the same Kubernetes
node while remaining separate processes/pods and trust boundaries. It does not
put the agent loop inside the tool sandbox.

Strict co-location requires scheduler/provider support: choose a compatible node,
provision/inspect the sandbox there, then admit the worker attachment with verified
node affinity. If unsupported or unschedulable, return an explicit pending/error
state. Do not claim strict co-location merely because both run in one cluster.
A profile can instead declare a preference with an explicit allowed fallback.

Separate execution keeps the worker in the service cluster and places the sandbox
on another node/cluster/provider. The provider's authenticated gateway/channel
carries tool requests/results; REST/SSE remains the application-facing interface.
OpenShell is the first provider to qualify for both policies. The current adapter
and chart provide substantial groundwork but not a portable placement guarantee.

## 5. Submitting work to a running cluster

Like submitting work to an existing compute cluster, callers choose an admitted
agent release and a RuntimeProfile or existing environment, then receive durable IDs:

```sh
ows context add dev --url http://127.0.0.1:8080 --namespace demo
ows context use dev
ows login --context dev --token-file /private/service-token
ows agent deploy --file coder.yaml --idempotency-key deploy-coder-v1
ows agent activate coder@v1 --idempotency-key activate-coder-v1
ows submit --agent coder@v1 --runtime-profile standard \
  --message 'Implement the requested feature.' --idempotency-key feature-456
ows watch --session SESSION_ID --run RUN_ID
ows send --session SESSION_ID --message 'Also add tests.' --idempotency-key followup-001
ows cancel --session SESSION_ID --run RUN_ID --idempotency-key cancel-001
```

`submit` atomically admits a new session and its initial run (or one durable
submission resource owning both), then returns IDs and `queued`/`provisioning`.
A worker starts only after the authorized environment is ready. It is not a
promise of immediate execution. The service generates unique session, run,
environment and operation IDs; these resource IDs are not caller tracing IDs.

The optional SDK `idempotencyKey` / CLI `--idempotency-key` maps to the REST
`Idempotency-Key` header. The SDK/CLI generates a key before the first mutation
when omitted and retains it across retries. Raw REST clients supply a key for
mutations; reject missing keys rather than silently invent deduplication behavior
that clients cannot recover. Callers supply a stable application key when they
need deduplication across application restarts. SDK-generated keys are returned
in admission handles/errors so an uncertain request can be inspected.

Principal + namespace + operation kind + idempotency key identifies admission;
the canonical payload hash detects conflicts. Same key and payload returns the
original receipt; a changed payload is `409`. Cross-namespace reuse cannot expose
receipts. Internal submission IDs remain stable and map to the same resource;
existing reference `request_id`/`submission_id` fields are translated internally,
not renamed silently on the old protocol. Preserve deduplication receipts for
the resource lifetime; advertise retention, and reject known expired keys rather
than treating them as permission to repeat an external action.

`requestId` / `X-Request-ID` is solely for tracing an individual HTTP attempt.
The service supplies a correlation ID in each response. It may differ between
retries and never controls deduplication. Audit records correlate request IDs,
resource IDs and the admission identity without storing credentials.

`send` submits another run on an existing session. V1 allows one active run per
session: conflicting work returns `409 session_busy`, not an invisible queue.
Explicit steering/approval/answer/cancellation remain typed control operations.
Workers prioritize controls even while a model/tool call is pending.

`watch` uses cursor-based SSE from any API replica, not a forwarded worker socket.
Reconnect resumes persisted events. Clients deduplicate event IDs and reconcile
provisional text by model-attempt ID. A retention gap returns an explicit cursor
expiration plus snapshot recovery instructions, never silent event loss.

### Unknown tool outcomes

After dispatch, loss of a tool connection is not evidence of failure or success.
Persist a structured `tool_outcome_unknown` response for the original call, with
its operation identity and whether it may still be running. Return it to the LLM
so it can read back state or check status before deciding whether to issue a new
action. Never automatically replay that original effect. Fresh calls still pass
policy/approval checks. Do not send secrets or raw transport internals to the model.

A live uncertain outcome may continue the current agent loop once recorded and
a safe control connection is available. User cancellation must not restart the
loop. Worker crashes/recovery retain their existing explicit continuation policy.
If status cannot be verified, preserve the unresolved outcome rather than infer
that the write failed. Normalize typed delivery uncertainty; do not label every validation/authentication
failure as “may have executed.” Storage unavailable means stop/fence execution,
not continue without recording the outcome. Existing recovery records this
condition; some live error paths still end the turn and require implementation.

## 6. Authentication, deployment gates and authorization

Authentication is mandatory, including local service testing. Bootstrap the
first administrator using a local install/job command and private credential
output, not an unauthenticated HTTP registration endpoint. Existing authorization
checks and audit transactions must remain effective during migration to `/v1`.

### First-administrator bootstrap: cloud and local

Cloud identity is optional. The root of trust is the operator who can install
and administer the infrastructure. Normal client authentication begins only after
the installer has created the first service administrator through a private,
privileged bootstrap path. There is no public “first caller becomes admin” route.

| Deployment | Installer authority | Initial credential delivery |
|---|---|---|
| Cloud Kubernetes | Existing cloud and Kubernetes permissions | Restricted cloud secret manager or Kubernetes Secret |
| Local/on-prem Kubernetes | Existing Kubernetes administrator credentials | Restricted Kubernetes Secret retrieved through the authorized Kubernetes connection |
| Docker / bare metal | Host administrator access | Owner-only local file, outside shared tool workspaces |

Sequence:

1. Install PostgreSQL/auth schema, endpoint trust and the service components.
   Terraform may provision infrastructure and secret-store locations; it is not
   required for an existing/local cluster.
2. Run a private bootstrap job/installer command with narrow access to the auth
   registry and designated credential destination. Generate a cryptographically
   random, expiring bootstrap token and establish `bootstrap-admin` as a service
   administrator. Persist only its token hash, principal, permissions and expiry
   in the authentication registry; deliver the token to the protected destination.
3. The operator retrieves it using installer authority (cloud secret-store access,
   Kubernetes RBAC, or local file ownership), not an unauthenticated service API.
   `ows login --url SERVICE_URL --token-file /private/bootstrap-token` presents
   that credential over the authenticated TLS connection.
4. The service validates it against the pre-existing administrator record. The
   administrator establishes normal human/service identities, tests another
   administrative login, then revokes the bootstrap credential.

Bootstrap uses a durable initialization marker and atomic uniqueness checks,
including concurrent job attempts. “No currently valid admin token” is not the
same as “never initialized”: token expiry/revocation never reopens bootstrap.
On retry, reconcile the original private bootstrap operation and credential
reference; do not generate/activate replacement admins opportunistically. Handle
secret-store and database partial failure explicitly: retain a retrievable
protected credential before exposing its administrator record, and never leave
a committed admin that the installer cannot retrieve. If ownership or receipt
state is uncertain, require private operator recovery rather than overwriting it.
Lost/expired first credentials require an audited infrastructure-admin recovery
procedure; there is no HTTP reset shortcut. Remove bootstrap-only permissions and
jobs after successful setup.

TLS server identity, API caller identity, and internal worker/OpenShell identity
are separate. Terraform/Helm configures endpoint certificates and their rotation;
internal mTLS credentials are not distributed as user login tokens. A local
cluster can use an operator CA trusted explicitly by CLI/SDK, or a managed public
certificate. Plain HTTP is limited to explicitly configured loopback development;
never disable TLS verification to support local boxes.

Do not generate/export the administrator bearer through Terraform state or its
outputs. Marking an output sensitive does not remove it from state. Terraform
provisions the destination and access policy; bootstrap writes the token directly.
Do not read its value back through a Terraform data source. Kubernetes Secrets
require restricted RBAC and appropriate encryption-at-rest; encoding alone is
not encryption. Local token files require owner-only access. Tokens never appear
in command arguments, browser storage, URLs, source control or installer logs.
CLI contexts store endpoint/namespace and a secure credential reference.

After bootstrap, the API's authentication/authorization behavior is identical
on cloud, local Kubernetes and Docker/bare metal.

### Caller identity versus agent permission

`Harness.start()` operates inside a trusted application/process boundary. It does
not require a service login, although its tools still follow runtime policy.
`Harness.connect()` crosses a service boundary and always authenticates the
application, user or service account making the request. An LLM is not an
independent login identity and does not choose or receive the caller's token.

Example: the product backend authenticates as `learning-app`. Its grant permits
running `coder@v1` and `verifier@v1` in project P using `team-workspace`, plus
attachment to project P's environments. That grant does not permit publishing a
new agent, changing RuntimeProfiles, viewing project Q, or using an arbitrary
provider. Human CLI/UI users authenticate as themselves with their own grants.

For each session/run admission check, in one authorized transaction where feasible:

1. The caller credential is valid and not expired/revoked.
2. The caller can execute this deployed, active agent release in this namespace/project.
3. The requested RuntimeProfile/model/provider use is allowed by caller and release policy.
4. Existing-environment ownership, attachment grants, sharing and compatibility match.
5. Quotas/capacity admission and the idempotency receipt permit the request.

Record the actor, release, effective grants and resolved binding. The worker may
execute only admitted work using its private worker identity; the runner receives
scoped execution credentials, not the user's service-admin or model-provider key.
Team-created child agents inherit only explicitly delegated, bounded authority
and approved releases, never wider permissions than the parent admission allows.

A backend acting for users must use service-verified delegation or scoped
principals/project grants. A caller-supplied `userId` is not authentication and
must not authorize access to another user's environment. Reads, SSE, reconnects,
approvals and cancellation enforce their own scopes; possession of a session or
environment ID grants nothing by itself. As already specified, revocation blocks
new admissions but does not pretend to undo an action already admitted.

Proposed namespace-scoped permission bundles (enforced as explicit permissions,
not hard-coded UI-only roles):

| Role | Authority |
|---|---|
| Viewer | Read authorized agents, sessions, events and operations. |
| Runner | Create sessions/runs and send permitted controls in granted environments. |
| Publisher | Upload/validate agent versions; cannot activate them by default. |
| Release administrator | Activate/retire agent versions and manage permitted RuntimeProfiles/provider configuration. |
| Service administrator | Manage credentials, quotas, worker-pool capacity and service configuration. |

Tool approvals are separate permissions, not implied by publisher/service-admin
access. Avoid issuing cluster-wide admin credentials to product backends. Existing
observer/operator/admin roles need an explicit compatibility mapping; unknown
permissions fail closed. Namespace isolation is authorization, not physical tenant
isolation; execution isolation remains separately enforced.

Deployments transition through `uploaded → validated → approved → active`, with
failure/rejection/retirement states. Validation checks bundle schema, tool and
provider bindings, image provenance policy, compatible worker cohorts and allowed
RuntimeProfiles. Activation is separately audited and can require a distinct
approver. No agent-created text can grant deployment permission. A new release
never mutates existing sessions. Emergency disablement and cancellation are
explicit operations with documented effects on pending/active work.

First implementation: expiring scoped service tokens with revocation and hashed
storage; show a newly issued token once. Production human administration: OIDC
login with issuer/audience validation and server-side group/permission mapping.
CLI login uses an authorization-code/PKCE or device flow where supported; context
files contain endpoint/namespace and credential references, not plaintext tokens.
Tokens are checked on every request/admission transaction and periodically during
SSE. Revocation prevents new admissions but does not undo already admitted effects;
use explicit cancellation. All mutations record actor, target, request ID and
outcome in durable audit without secrets or model content.

## 7. Shared REST management API

Version the public contract under `/v1`. Reuse current commands/receipts internally;
keep the old reference API separate until a documented deprecation. Publish an
OpenAPI description and SSE event schemas, including limits and structured errors.

| Resource | Operations |
|---|---|
| `/capabilities`, `/auth/me` | Version/features/limits and caller grants. |
| `/agents`, `/agents/{id}/versions` | List, upload declarative versions, validate and inspect. |
| `/agents/{id}/versions/{version}/activate` | Separate authorized activation command. |
| `/execution-providers` | Register/validate execution backends and their credential references. |
| `/environments`, `/runtime-profiles` | Provision/attach metadata, inspect, release/delete and reconcile. |
| `/submissions` | Atomic new-session + initial-run admission and receipt lookup. |
| `/sessions`, `/sessions/{id}/turns` | Session creation/read and additional run admission. |
| `/sessions/{id}/controls`, `/sessions/{id}/events` | Priority controls, receipts and cursor-based SSE. |
| `/sessions/{id}/artifacts`, `/sessions/{id}/artifacts/{artifactId}` | Register pending output and inspect session-scoped metadata. |
| `/sessions/{id}/artifacts/{artifactId}/content`, `/sessions/{id}/artifacts/{artifactId}/reconcile` | Bounded authenticated transfer and verification of the same stored bytes. |
| `/model-providers`, `/model-providers/{id}/credentials` | Validated provider revisions, write-only secret updates, health checks. |
| `/policies`, `/worker-pools`, `/configuration` | Validated revisions, desired capacity and operational limits. |
| `/operations/{id}` | Durable `accepted/applying/applied/failed/unknown` status and reconciliation. |
| `/credentials`, `/audit` | Issue/revoke service identities and read authorized audit. |

Reads are scoped before pagination. Writes use idempotency keys and payload
conflict checks; request IDs are separate per-attempt tracing metadata.
Configuration edits also require an expected revision/ETag to reject lost updates. Responses separate admission, desired state and observed/applied state.
Long operations return `202` plus an operation ID; never report success merely
because a Kubernetes/provider request was submitted. Deletes respect active
references and retention policy. Errors include a safe code, request ID and retry
classification, never access tokens or raw upstream responses.

The implemented artifact endpoints reuse the existing [artifact contract](../../harness/artifacts/README.md):
registration's stable `request_id` identifies an exact file/session/turn and may
be repeated with identical parameters. Changed parameters conflict. A transfer
failure never authorizes regenerating a file or replaying its producing tool.
Managed `/v1` endpoints retain namespace and session authority checks; enabling
artifacts does not enable legacy routes. Storage credentials belong to the trusted
service, independently of the agent's sandbox and model credentials.

### Providers, secrets and compaction

Model-provider records contain display name, API family (Responses, Chat
Completions, Anthropic), endpoint and allowed models. Their credentials are
separate write-only resources backed by an approved secret store (Kubernetes
Secrets or cloud secret manager adapter). Metadata returns presence/version and
health, never plaintext. Cluster mode must not use a worker's personal OS keychain.

Rotating a credential keeps its logical provider identity; requests use the
current valid secret version. Changing API/endpoint or routing identity creates
a new provider configuration revision. Existing sessions retain their resolved
model/configuration identity; never silently substitute another model.

Compaction defaults, context limits, run budgets and concurrency limits are
validated structured configuration. Effective configuration is resolved from
service policy → namespace policy → agent release → permitted session overrides.
Hard security ceilings can only narrow; ordinary defaults may be overridden
within their allowed range. Record the resolved revision on session creation.
Changes apply to new sessions unless an explicit, audited active-session update
is supported at a safe boundary. Credential rotation is intentionally different.
Reviewer model/provider settings remain distinct from the working agent's model.

### Scaling and infrastructure ownership

Terraform (or another IaC tool) provisions the cloud account resources: VPC/subnets,
cluster, node-pool instance types and min/max capacity, IAM, database and storage.
Helm installs the service, UI and worker pools on that infrastructure. Node/pod
autoscalers then adjust within operator-approved bounds; Terraform should not
continually force a live replica count owned by an autoscaler.

The service API/`ows`/UI controls logical worker capacity, worker slots, admission
quotas and execution RuntimeProfiles within those bounds. It does not create cloud
nodes or expand the operator's spending ceiling through ordinary agent requests.
If Kubernetes lacks capacity, report pending/blocked scheduling; node autoscaling
may satisfy it within its configured limits. Expanding those limits or adding a
new cloud/provider remains an infrastructure/operator change. External execution
provider capacity and costs are separately bounded by its registered profiles.

`ows pool scale coder --replicas 4 --idempotency-key scale-001` writes desired state;
the platform controller reconciles it and reports observed capacity/readiness.
Also expose drain, pause admissions, namespace quotas, worker slots and maximum
queued work. Scaling workers does not scale execution-provider sandboxes or
cloud nodes automatically; expose those capacities separately.

Scale-down drains workers under the existing drain contract. Surface incompatible
active sessions/old cohorts, timeout and blocked operations. Forced interruption
requires an explicit privileged request; it must not manufacture successful tool
results or auto-replay uncertain effects. Scale-to-zero preserves durable state
and clearly reports queued work. Retain compatible capacity for pinned sessions.

Each pool has one replica authority: `manual`, `autoscaled`, or `gitops`. Manual
REST scaling conflicts explicitly with an HPA/GitOps-owned pool. GitOps-managed
configuration may expose read/plan only; do not allow the UI and Helm/Argo to
continually overwrite each other. Node autoscaling remains the cloud operator's
responsibility, subject to declared provider capacity limits.

## 8. Management UI and network boundary

The UI runs as a separate pod and uses the same REST/SSE contract as `ows` and the
SDK. It has no database access and no embedded cluster-admin credentials. Provide
agent release review, sessions/runs/approvals, environment status, provider setup,
compaction/budget configuration, pool scaling/draining, audit and operations.
Permissions are enforced by the API even if a UI control is hidden.

Serve UI and API behind TLS ingress (for example `/` and `/api`). Browser human
login uses an OIDC-backed BFF with Secure/HttpOnly/SameSite session cookies and
CSRF protection; the BFF forwards short-lived caller-scoped service credentials.
Never place long-lived administrative tokens in browser localStorage. Direct API
browser access, if offered, needs explicit origin/CORS policy and a short-lived
identity flow. The current reference rejects browser Origin requests: replace
that only for the intentional management path, not by allowing every origin.

Private network policy separates API, workers, database, secret store, artifact
store, controller and execution gateways. The controller's Kubernetes service
account can patch only the managed pool resources in its namespace, not arbitrary
pods/secrets/cluster RBAC. API requests cannot supply raw Kubernetes manifests,
host paths, shell commands or unrestricted cloud credentials as management actions.
Application-native tools inside a remote worker are trusted deployed code.

## 9. Local Docker profile

Provide a new `deploy/service/compose.yaml` (proposed) containing service API,
worker, PostgreSQL and optional UI/controller. Optional object storage and telemetry
collectors use profiles. Same authentication, API and event semantics as production;
single-host development is not HA. Persist database and workspace data separately.

```sh
ows dev up --directory .ows-dev                 # explicit local development stack
ows login --context local --token-file .ows-dev/admin-token
ows agent deploy --file examples/coder.yaml --idempotency-key local-coder-v1
ows agent activate coder@v1 --idempotency-key local-activate-v1
ows submit --agent coder@v1 --runtime-profile local-dev \
  --message 'Build a quiz app' --idempotency-key local-quiz-001
ows dev down                                  # preserves volumes by default
```

`dev up` is a local infrastructure command invoking Compose, not a REST request
to an already-running service. Bootstrap credentials are written once to a private
file; never printed into Compose logs. Ports bind localhost. Default profiles use
scripted/native safe fixtures; workspace shell execution requires explicit opt-in
and a dedicated development worker/container. No host home mounts or Docker socket
inside API/worker pods/containers. Clearly identify that a shared unsandboxed
worker container is not tenant isolation. Production shell workloads require an
approved isolated execution profile.

To exercise option 3 locally, use a separately started runner container via the
new authenticated local provider adapter, or the independently installed OpenShell
gateway. Docker-only remote attach is new work; never silently fall back to host
execution. The operator/Compose owns runner lifecycle initially, so no Docker
socket is needed in the service. API-only local provider capacity changes report
unsupported unless a deliberately installed controller supports them.

## 10. Kubernetes and cloud installation

Extend the existing chart with independently deployable API, worker pools,
management UI/BFF and a narrowly privileged controller. Use external PostgreSQL
and, when enabled, durable artifact storage. Backing services and OpenShell remain
independently managed components; validate compatibility and required policy.
Set requests/limits, startup/readiness/liveness probes, graceful termination,
network policy and private worker identities. Observe queue latency, slots,
leases, unknown outcomes, provider errors and configuration reconciliation via OTel.

Install into an existing cluster first:

```sh
helm upgrade --install openworker ./deploy/helm/openworker \
  --namespace openworker --create-namespace --values production-values.yaml
ows context add production --url https://agents.example.com --namespace product-a
ows context use production
ows login --context production
```

The Helm command describes the existing chart installation mechanism; new service
values/resources arrive in the implementation milestones. Bootstrap/migrations
are explicit versioned jobs run by an installer identity. Never use a publicly
reachable first-admin bootstrap route. Secrets are referenced from trusted stores.
UI deployment is optional; CLI/API users have identical capabilities.

Terraform is the recommended infrastructure reference, not a runtime dependency:
customers may bring an existing cluster provisioned by any supported process.
Separate future infrastructure modules can provision EKS/AKS, VPC/subnets, database,
identity, storage, certificates and the chart. They must present an account/region,
resource plan, estimated cost controls and cleanup ownership before applying.
Use the operator's short-lived cloud identity; do not accept an AWS/Azure master
credential through the service UI. Installing into an existing cluster and
creating a whole cloud cluster are distinct operations. Existing AWS experiment
scripts are qualification tools, not an unrestricted customer provisioner.

## 11. Implementation sequence and acceptance gates

1. **Contract and authenticated remote client.** Define `/v1`, capabilities,
   receipts/error schemas and namespace grants; add `Harness.connect()` transport
   in Rust/TypeScript over existing admission/event semantics. Verify auth denial,
   lost acknowledgments, duplicate admission, reconnect to another API, controls
   and read-only disconnect. Local SDK remains compatible.
2. **Agent releases and environment bindings.** Implement deploy/validate/activate,
   grants, immutable release resolution, RuntimeProfiles and asynchronous
   provisioning receipts. Verify an undeployed/unapproved agent cannot run, tool
   exposure cannot exceed deployed grants, and uncertain create/attach cannot
   duplicate environments. Verify returned environment IDs attach a second
   authorized agent, keep private conversation/memory separate, reject unauthorized
   or exclusive attachments, and preserve the other session when one detaches. Normalize live unknown tool outcomes and test model
   read-before-retry with no automatic replay and no continuation after cancellation.
3. **`ows` plus Docker reference.** Context/login/agent/submit/send/watch/cancel,
   operation inspection and idempotent dev lifecycle. Qualify clean Docker setup,
   private persistence, same-build restart, explicit execution limits and direct
   SDK versus CLI equivalence. No cloud account required.
4. **Management operations and Kubernetes controller.** Versioned provider/secret,
   compaction/quota configuration; manual/GitOps/HPA ownership; scaling/drain
   receipts. Verify credential revocation across replicas, secret rotation without
   disclosure, conflicts, controller restart, scale-down and capacity recovery.
5. **Management UI and public-cloud reference.** UI uses the already tested API;
   qualify human auth/CSRF/role separation, cross-namespace denial and audit.
   Run multi-node failure/upgrade drills with version-pinned pools and external
   execution. Cloud provisioning and each new provider adapter get separate gates.

Every milestone updates the handoff/status ledger. “Accepted”, “applied”, “run
completed”, and “external effect confirmed” remain distinct throughout. No new
second agent loop, silent security fallback, implicit deployment from an ordinary
runner credential, or transparent retry of uncertain tool effects.
