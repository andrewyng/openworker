# OpenWorker Service: connection, deployment and control plane

Status: design proposal for implementation review · 27 October 2026

This specifies the next service interface. New SDK methods, CLI commands and
`/v1` routes below are proposed, not a claim that the current server reference
implements them. Existing local SDK/stdio APIs remain supported.

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

Gaps: a remote transport for the friendly SDK; deployed-agent registry and release
admission; namespace-aware management permissions; expiring credentials/human
login; declarative management operations/controllers; a packaged Docker-only
service profile; and a writable management UI. The current local reference uses
host processes plus backing-service containers and a separate Kubernetes/OpenShell
gateway. Do not describe it as the Docker-only product specified here.

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
  requestId: 'workspace-42-session',
  environment: { id: 'env_workspace_42' },
  // Optional; must be allowed by the deployed agent and provider grants.
  model: { provider: 'primary', name: modelId },
});
const run = await session.send('Implement the requested feature.', {
  submissionId: 'feature-456',
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
  defaults and permitted environment profiles. Teams use the same release model.
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

## 4. Execution-environment selection

`newSession()` accepts exactly one of:

```typescript
environment: { id: 'env_existing' }                 // attach an admitted environment
environment: { profile: 'isolated-linux-small' }    // provision from an approved profile
```

If omitted, use the deployed agent's authorized default profile. If no default
exists, return a validation error. Never silently use the API/worker filesystem.
The server resolves namespace ownership, caller grants, agent compatibility,
image/runner identity, sharing mode, resource/network policy and capacity.

An environment profile is an immutable operator-managed configuration: provider,
image digest, workspace persistence, CPU/memory limits, network/filesystem grants,
credential references, lifetime and exclusive/shared attachment policy. Caller
resource overrides are bounded by that profile. An environment ID is not a
credential and does not itself authorize access.

Environment creation has a durable operation ID and idempotency key. A session
request can return `202 provisioning` with its session/operation IDs; the SDK
waits for readiness unless cancelled by the caller. Cancelling the wait does not
cancel provisioning. An unknown provisioning result is reconciled with the
original remote identity; it is never replaced by a second environment.

Default: one exclusive workspace per session or explicitly authorized project.
Shared environments require explicit grants and share filesystem/network
permissions; they are unsuitable for mutually untrusted learners. Closing a
session releases its attachment. Environment deletion and workspace deletion are
separate explicit operations, never consequences of a client disconnect.

LLM credentials stay on workers. ToolRunner receives only credentials scoped to
its permitted tools/resources. External providers may use authenticated outbound
connections or a verified private gateway; no generic inbound public ToolRunner
port is required. Provider-specific connectivity and identity must be qualified.
Modal/Fly.io integration is an adapter milestone, not assumed support.

## 5. Submitting work to a running cluster

Like submitting work to an existing compute cluster, callers choose an admitted
agent release and an environment/profile, then receive durable IDs:

```sh
ows context add dev --url http://127.0.0.1:8080 --namespace demo
ows context use dev
ows login --context dev --token-file /private/service-token
ows agent deploy --file coder.yaml --request-id deploy-coder-v1
ows agent activate coder@v1 --request-id activate-coder-v1
ows submit --agent coder@v1 --environment env_workspace_42 \
  --message 'Implement the requested feature.' --request-id feature-456
ows watch --session SESSION_ID --run RUN_ID
ows send --session SESSION_ID --message 'Also add tests.' --request-id followup-001
ows cancel --session SESSION_ID --run RUN_ID --request-id cancel-001
```

`submit` atomically admits a new session and its initial run (or one durable
submission resource owning both), then returns IDs and `queued`/`provisioning`.
A worker starts only after the authorized environment is ready. It is not a
promise of immediate execution. Request ID plus principal/namespace and payload
hash uniquely identifies admission. Exact repeats return the original receipt;
a changed payload under the same ID is a conflict. Cross-namespace reuse does
not expose receipts. SDK-generated IDs are stable across transport retries.

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

Proposed namespace-scoped permission bundles (enforced as explicit permissions,
not hard-coded UI-only roles):

| Role | Authority |
|---|---|
| Viewer | Read authorized agents, sessions, events and operations. |
| Runner | Create sessions/runs and send permitted controls in granted environments. |
| Publisher | Upload/validate agent versions; cannot activate them by default. |
| Release administrator | Activate/retire agent versions and manage permitted profiles/provider configuration. |
| Service administrator | Manage credentials, quotas, worker-pool capacity and service configuration. |

Tool approvals are separate permissions, not implied by publisher/service-admin
access. Avoid issuing cluster-wide admin credentials to product backends. Existing
observer/operator/admin roles need an explicit compatibility mapping; unknown
permissions fail closed. Namespace isolation is authorization, not physical tenant
isolation; execution isolation remains separately enforced.

Deployments transition through `uploaded → validated → approved → active`, with
failure/rejection/retirement states. Validation checks bundle schema, tool and
provider bindings, image provenance policy, compatible worker cohorts and allowed
environment profiles. Activation is separately audited and can require a distinct
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
| `/environments`, `/environment-profiles` | Provision/attach metadata, inspect, release/delete and reconcile. |
| `/submissions` | Atomic new-session + initial-run admission and receipt lookup. |
| `/sessions`, `/sessions/{id}/turns` | Session creation/read and additional run admission. |
| `/sessions/{id}/controls`, `/sessions/{id}/events` | Priority controls, receipts and cursor-based SSE. |
| `/providers`, `/providers/{id}/credentials` | Validated provider revisions, write-only secret updates, health checks. |
| `/policies`, `/worker-pools`, `/configuration` | Validated revisions, desired capacity and operational limits. |
| `/operations/{id}` | Durable `accepted/applying/applied/failed/unknown` status and reconciliation. |
| `/credentials`, `/audit` | Issue/revoke service identities and read authorized audit. |

Reads are scoped before pagination. Writes use request IDs and payload conflict
checks. Configuration edits also require an expected revision/ETag to reject lost
updates. Responses separate admission, desired state and observed/applied state.
Long operations return `202` plus an operation ID; never report success merely
because a Kubernetes/provider request was submitted. Deletes respect active
references and retention policy. Errors include a safe code, request ID and retry
classification, never access tokens or raw upstream responses.

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

### Scaling

`ows pool scale coder --replicas 4 --request-id scale-001` writes desired state;
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
ows agent deploy --file examples/coder.yaml --request-id local-coder-v1
ows agent activate coder@v1 --request-id local-activate-v1
ows submit --agent coder@v1 --environment-profile local-dev \
  --message 'Build a quiz app' --request-id local-quiz-001
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
   grants, immutable release resolution, environment profiles and asynchronous
   provisioning receipts. Verify an undeployed/unapproved agent cannot run, tool
   exposure cannot exceed deployed grants, and uncertain create/attach cannot
   duplicate environments. Normalize live unknown tool outcomes and test model
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
