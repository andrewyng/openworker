# OpenWorker Service progress ledger

## 2026-10-27 — M1 started

- Created feature branch `feat/openworker-service-m1`; public SDK branch unchanged.
- Read existing REST/SSE reference, PostgreSQL admission/auth, execution provider
  contract and local SDK transports. Reuse these boundaries.
- Created [implementation plan](SERVICE-IMPLEMENTATION-PLAN.md).
- Disk baseline: 38 GiB available; reuse native Cargo target (12 GiB).
- M1 deliberately begins with configured agent/environment bindings. RuntimeProfile
  creation, deployment gates and full management auth/UI remain later milestones,
  advertised as unsupported rather than stubbed success.

Initial state: tests not yet run. See the milestone entry below for results.

## 2026-10-27 — M1 implementation and qualification

Implemented:

- Opt-in `/v1` capabilities, identity, configured-agent lookup, session creation,
  snapshots, idempotent turn admission, run results, controls/receipts and SSE.
- Persistent service/namespace binding; existing PostgreSQL role, ownership and
  environment grants remain authoritative. No public bootstrap route.
- Rust and TypeScript `Harness.connect()` with remote session/run handles,
  credential callbacks, endpoint identity checks, bounded event parsing and cursor
  reconnection. Disconnect closes client connections, not worker execution.
- Mutation transport failures retain their idempotency key and never trigger an
  automatic resubmission. The legacy queue API and local SDK transports remain.
- [M1 developer setup and API examples](SERVICE-M1-QUICKSTART.md).

Issues found during actual qualification:

1. Mac controller validation incorrectly required a Mach-O ToolRunner even when
   OpenShell executes that runner in Linux. Validation now follows execution
   location; unsandboxed runners still require the host executable format.
2. A remote run iterator stopped on `turn_recovered`, although a worker can recover
   a parked turn and continue after applying approval. Both SDKs now stop only on
   terminal recovery statuses. The end-to-end test covers replay through recovery
   to the real tool result.
3. The existing cached Linux qualification image predates the `example_echo`
   example-tool rename. The test builds a temporary bundle with supported file and
   shell tools. It uses the cached pinned runner unchanged; no large image rebuild.

Verification completed so far:

- TypeScript: 17 existing configuration/stdio transport tests, 6 service tests.
- Rust SDK: 16 unit tests; desktop API suite 5 passed (embedded SQLite,
  managed process, ephemeral state, provider/reviewer setup, callback crash).
  The two PostgreSQL desktop variants are separately opt-in and not counted.
- PostgreSQL: 3 ingress, 8 submission, and 1 service-identity/recovery regression
  tests; 5 adapter unit tests. The identity test rejects writes via a database
  trigger and verifies that restore identity checks still succeed.
- Platform: execution-location runner-format test passed.
- Server reference: 4 existing unit tests passed. Recovery-only startup verifies
  the persisted service binding without inserting or updating it.
- Real service qualification passed against disposable Docker PostgreSQL,
  two native API processes, a native worker and a sandbox on the existing local
  Kubernetes/OpenShell gateway. Deterministic model, real file write/read.
  Rust creates/submits/approves, original API exits, TypeScript resumes and reads
  output, replays events, cancels a subsequent pending write, and reads old results.
- Final augmented real-service run passed (19.75 seconds), including observer
  mutation denial, cross-owner access, missing environment grants and result text.

Local resources: reused `postgres:16-alpine` with a 256 MiB tmpfs and existing
OpenShell runner image. Failed-attempt sandboxes were explicitly deleted; successful
fixture cleans its sandbox and schema. No existing DLAI/other workloads changed.
Disk available increased to ~71 GiB during the session; no extra space needed.

Remaining after M1: deployed agent releases, RuntimeProfile API/provisioning,
shared-environment attachment management, `ows`, packaged local Docker stack,
expanded auth/configuration, Kubernetes control plane and UI. Existing OpenShell
integration is reused; this milestone does not claim those management APIs exist.

Cleanup complete: dedicated test sandboxes removed; only the pre-existing suspended
sandbox remains in the gateway namespace. Disposable PostgreSQL container stopped,
gateway port-forward stopped and temporary copied client certificates removed.
Final disk measurement: 68 GiB available. No cloud spending or new container images.

M1 complete. Changes are local on `feat/openworker-service-m1`; no public branch
push or pull request is part of this milestone.

## 2026-10-27 — M2 started

User authorized M2 and optionally M3, with local tests and ledger updates.
Working on `feat/openworker-service-m1` after `c231d102`; initial disk free 68 GiB.
No LLM key needed for deterministic execution/recovery qualification.
Architecture: extend the existing PG store, controller/provider and worker loop;
no second agent loop and no remote code execution on API nodes.

### M2 implemented; qualification in progress

- Added immutable, revisioned release/profile catalog, explicit activation aliases,
  principal allowlists and management idempotency receipts.
- Session admission atomically reserves operation/environment/session identities.
  A separate controller provisions and verifies OpenShell, then atomically creates
  the owned session and completes its receipt. Lost dispatches reconcile only.
- Dynamic cohorts reuse the existing Worker loop under one total slot limit;
  adding releases does not multiply configured worker concurrency.
- Profiles configure verified CPU/RAM, sharing, approval mode and question skipping.
  Releases select installed tools, model defaults/allowed overrides, and a separate
  reviewer model. Shared attachments require matching owner/project/profile.
- Rust and TypeScript SDK helpers support release/profile management, automatic
  provisioning waits, explicit operation lookup and same-environment attachment.
- Initial real local qualification passed: Docker PostgreSQL, two APIs, two
  controllers, shared coder/verifier sandbox, both SDKs and independent histories.
- Fault-injected lost provisioning acknowledgement also passed: the controller
  binds the original remote UID; SDK wait timeout resumes the same receipt.
- Storage regression suites passed (18 tests across environment/admission/submission
  suites), plus three OpenShell policy/resource identity tests. Additional quota,
  default resolution and live tool-uncertainty checks are being finalized.

M2 intentionally uses the qualified fixed OpenShell filesystem/network profile.
Advanced external-sandbox import, directory overrides, alternate providers and team
release packaging are not advertised as available. See SERVICE-M2-QUICKSTART.md.
Disk availability is now >200 GiB; this work did not require extra image downloads.


### M2 qualification complete

Final exercised behavior:

- Immutable revisions, explicit activation and alias changes that preserve existing
  admissions; default profile resolution; model override allowlists.
- Profile/environment ownership and project checks, environment quota enforcement,
  and replay of an admitted request even when the quota is full.
- Atomic admission rollback, one create authorization, and installation-compatible
  controller claims. Workers retain bounded slots across dynamic cohorts and skip
  releases from incompatible installed worker/runner cohorts.
- Real OpenShell creation with custom RAM limit, sharing between coder/verifier,
  private session histories, and a separately provisioned environment for another
  project. Original remote UID retained after lost provisioning acknowledgement.
- Rust and TypeScript SDKs recover timed-out waits using the same operation/key;
  resumed references reject an incorrect agent release. Wait transport errors
  retain operation identity rather than losing the accepted admission.
- Fault-injected live tool test: one effect, lost response, durable unknown result,
  subsequent model-directed read, and no replay of the original write. This reuses
  the existing RemoteClient normalization; unrelated storage/configuration errors
  continue to stop execution.

Tests passed: 3 new catalog tests; 5 environment, 3 ingress and 8 submission
regressions; 3 OpenShell identity/resource-policy tests; 16 Rust SDK unit tests;
7 desktop SDK tests including PostgreSQL embedded/managed variants; 4 server and
5 store unit tests; 17 existing TypeScript tests plus 7 remote-service tests.
The real M1 API/control regression, real M2 provisioning/sharing qualification,
and live uncertainty fault test also passed. Models were scripted; tools and
OpenShell provisioning were real except the explicitly fault-injected provider.
No paid model calls or LLM key were needed.

Developer instructions: [M2 quickstart](SERVICE-M2-QUICKSTART.md).
M3 CLI/container packaging is the next milestone; it is not included in this work.
Advanced sandbox import, provider-specific disk recovery, directory overrides,
extra network/credential policy, team-release deployment and expanded auth remain
explicit limits, not claimed completed features.

M2 cleanup complete: successful fixtures deleted their sandboxes and schemas; only
the pre-existing suspended sandbox remains in the gateway namespace. Disposable
PostgreSQL, port forwarding and temporary client certificates were removed.
Final real M2 qualification took 35.66 seconds. All final checks passed.
M2 is committed locally on `feat/openworker-service-m1`; no remote push/PR yet.

## 2026-10-27 — M3 started

User authorized `ows` and local container packaging with end-to-end testing.
Continue on `feat/openworker-service-m1` from `273694e8`.
Disk baseline: 216 GiB free. Reuse cached PostgreSQL/nginx/Rust images and existing
OpenShell 0.1.3 gateway; do not alter the default kubecontext or other workloads.

Implementation order: atomic session + initial task admission; thin Rust CLI using
service SDK; containerized service roles with private PostgreSQL/TLS ingress;
CLI-driven local qualification, restart/reconnect and explicit cleanup.
OpenShell remains a separately configured provider; the package will not claim to
install a second gateway or a new Kubernetes cluster as a hidden side effect.

### M3 implementation and qualification

Implemented combined admission (`POST /v1/submissions`), reserving operation,
session, environment and initial run IDs before provisioning. Session creation,
first queued task and completion receipt commit atomically. Added `Service.submit`
in both SDKs and the thin Rust `ows` CLI for catalog, task and control operations.

Local Compose package now runs PostgreSQL/TLS, a private bootstrap job, API,
controller, worker and loopback TLS ingress. It pins the service image identity,
separates bootstrap/model/gateway secrets by role and retains state across down/up.
OpenShell is an explicit prerequisite; this package does not install a hidden
cluster or alter existing kubecontexts.

Qualification found and fixed two packaging/architecture problems:
- Docker Desktop did not publish ingress from an internal-only network; ingress
  now has a separate network while PostgreSQL remains private.
- Initial deletion attempted gateway I/O on API nodes, which have no gateway
  credentials. Deletion now queues on the API and dispatches from the private
  controller, once only. Subsequent claims reconcile the original object.
The failed first fixture was explicitly removed through Kubernetes; the corrected
implementation is being tested with a fresh environment, without manual cleanup.

Passing regressions so far: 4 PostgreSQL catalog tests (including atomic rollback,
close fencing and one delete-dispatch authorization), 8 submission tests, 16 Rust
SDK unit tests, 2 CLI configuration tests and 8 TypeScript service tests. Linux
ARM64 release binaries built with pinned Rust 1.96.1 container; native macOS CLI
built with the workspace toolchain. No model credentials or paid model calls used.

### M3 qualification complete

The final CLI-driven run passed against the Compose service and local Kubernetes
OpenShell gateway. It created a fresh environment and completed all cleanup through
the corrected service API/controller; no manual deletion was needed for that run.

Verified:
- Native `ows` context add/login/use and persisted service identity; packaged Linux
  CLI also authenticated successfully. Wrong CA and wrong bearer token rejected.
- Immutable release/profile publish + activation, combined submission replay with
  the same run/session/environment IDs, changed-body conflict.
- Real sandboxed write/read with output bytes verified in replayed SSE events.
- Pending approval survived API + worker restart and executed only after approval.
- Durable question/answer, cancellation of a pending write, and full Compose
  down/up retaining the original session and turn.
- Safe close, asynchronous controller deletion, confirmed remote absence, and
  refusal to destroy a deployment under the wrong identity.
- PostgreSQL fault test proves session/task/receipt rollback together; deletion
  claim tests prove installation matching and no second dispatch after a lost claim.

Final test counts: 4 PostgreSQL catalog + 8 submission tests, 16 Rust SDK unit
checks, 2 CLI configuration tests, 8 TypeScript service tests, plus the real
container qualification and Linux CLI smoke check. The server reference's existing
unit suite was rerun as a final regression check. Models were scripted; all noted
OpenShell, storage, TLS and tool effects were real. No live-model quality claim.

Local qualification deployment: `ows-5ffe2199f562`.
Final session: `s_30705acd25884df5be55dfe9879be743`.
Final run: `t_ea43c8d896bb4888b63024d815f6d8ef`.

Docs: [CLI quickstart](SERVICE-M3-QUICKSTART.md),
[local package](../../deploy/service/README.md).

Boundaries: one local namespace; static bootstrap credential; ARM64 container and
macOS-host CLI qualification. OpenShell/Kubernetes are separately provisioned.
Multi-architecture publication, hosted identity/expiring tokens, managed service
configuration/scaling, Kubernetes management packaging and cloud qualification are
not included. The source-build Docker target is provided; qualification used the
same pinned builder command with a persistent cache, then the prebuilt image target.

M3 cleanup complete: Compose containers/networks/volumes removed through the guarded
installer, disposable unit-test PostgreSQL stopped, gateway port-forward stopped,
and generated/copied test credentials removed. Only the pre-existing suspended
OpenShell sandbox remains. Build cache and local images retained for subsequent
work (Linux cache/output ~840 MiB); final disk free ~213 GiB. No cloud resources or
unrelated workloads changed. All 4 server-reference unit tests passed as well.

M3 is complete locally on `feat/openworker-service-m1`; no remote push or PR.
Next is M4: expiring credentials/bootstrap administration, managed configuration
and scaling/draining, then Kubernetes control-plane packaging and qualification.

## 2026-10-27 — M4 started

User authorized the next milestone; AWS and GCP clusters will follow later for
cloud qualification. Continue from `a657c92f` on `feat/openworker-service-m1`.
Disk baseline: 213 GiB free. Reuse existing Rust/Linux caches, local Docker/K8s and
OpenShell. No cloud infrastructure or unrelated deployments will be changed.

Work order: credential expiry/deployment permissions; managed configuration and
capacity; Kubernetes packaging/controller and graceful drain; local qualification.
Reuse existing PostgreSQL auth/audit, worker drain and Helm machinery.

### M4 implementation in progress

- Added database-time credential expiry, separate publisher/release/service-admin
  roles, expiring private bootstrap and audited infrastructure-only recovery.
  Legacy administrator compatibility remains explicit; new service-admin grants
  do not grant tool approvals. Public v1 credential issuance requires expiry.
- Added expected-revision/idempotent service configuration, admission pause and
  bounded quotas. Compaction defaults are pinned at admission and passed to session
  creation; existing sessions retain their resolved settings.
- Added worker-pool desired/observed state with immutable deployment UID binding,
  replica ceilings and explicit manual/GitOps/HPA ownership. Private Kubernetes
  reconciler has no public arbitrary Kubernetes proxy/manifest interface.
- Service workers now use existing graceful-drain semantics and dependency-gated
  readiness. SIGTERM stops intake; a second signal/deadline interrupts explicitly.
- Local database checks passed: 8 authorization, 6 catalog/configuration/pool and
  8 submission regressions. ARM64 service and matching ToolRunner images built;
  local Kubernetes Helm qualification is next. No paid model calls/cloud changes.

Kubernetes packaging qualification found projected-file compatibility: Kubernetes
ConfigMaps/Secrets are symlinks, while general runtime config readers intentionally
reject symlinks. Static configuration/bootstrap inputs now use direct subPath file
mounts. The platform controller has a separate bounded reader for Kubernetes-owned
rotating service-account projections. General coworker/tool file checks remain.
The first install is a failed test fixture; no agent task has run on it yet.

The first CLI-through-HTTP permission test caught an additional boundary mismatch:
publishers could write definitions in the store but could not inspect a referenced
RuntimeProfile because its execution allowlist was applied to management reads.
Deployment roles can now inspect catalog metadata; execution admission still
requires runner permission and the principal allowlist. Inactive releases also
have an explicit metadata read route, separate from the active-agent route.
Credential CLI output now uses a distinct `--out` argument ID; a parser regression
ensures it cannot replace the caller's `--token-file` authentication input.

User decision during M4: service tokens for now; browser SSO/OIDC will be added
later. No identity-provider credentials or tenant setup are needed for this milestone.

The live scale-to-zero check exposed Kubernetes' valid `Scale.spec: {}` encoding
for zero replicas. The controller now reads omission as zero, rejects malformed
counts and has a regression using the observed Kubernetes response. Capacity
observations also expire to `unknown` after controller silence rather than showing
stale `applied` status. Final qualification additionally rotates/revokes bootstrap,
removes its Secret, and verifies a service administrator cannot cancel agent work.

Final boundary review also closed two service-profile escape paths: recovery-only
mode now rejects all service worker/controller roles before DB/provider access;
and legacy reference routes/configured-agent admission require explicit
`legacy_compatibility: true`. Managed Compose/Helm profiles disable compatibility,
so callers cannot bypass release activation or v1 credential expiry through older
endpoints. The M1 configured-agent test/document explicitly opts into compatibility.
Deployment/configuration/capacity audit records now include their target IDs.


### M4 qualification complete

Final package passed the full CLI/HTTPS acceptance suite on local kind Kubernetes
v1.36.4 with the existing OpenShell 0.1.3 gateway. Actual worker pods and remote
ToolRunner file/shell actions were exercised; the model was deterministic.
No LLM key, paid model call, cloud resources or unrelated namespace changes.

Verified end to end:
- Expiring service credentials, rotation/revocation of the original bootstrap
  credential, removal of its Secret, and service-admin denial of agent control.
- Publisher metadata access and publication, separate release activation, and
  strict refusal of legacy routes/direct configured-agent admission.
- Expected-revision configuration, admission pause and already-admitted execution;
  pinned compaction defaults verified both through metadata and stored session data.
- Submission with zero workers, scale-up execution, real write/read output,
  and a running shell action completing successfully during scale-down drain.
- Scale 0/1/2, Helm upgrades preserving manual replicas, stale observations becoming
  unknown while the controller is stopped, and recovery of the original pool/UID.
- API rollout/reconnection with durable session identity and results.
- Targeted publication/activation audit records; explicit session close and
  controller-driven sandbox deletion.
- Guarded namespace teardown: wrong identity rejected; API quiesced before the
  final resource check; dedicated namespace/PVC removed. Original gateway untouched.
- Packaged Linux CLI authenticated over verified TLS using the existing read-only
  probe credential. Packaged recovery-only platform controller rejected execution
  before database/cluster access, with networking disabled for that negative check.

Regression results: 8 PostgreSQL authorization tests, 7 service catalog/config/pool
checks, 8 submission tests; 6 server-library and 3 server-command tests; 3 CLI tests;
3 Helm boundary/ownership tests; 7 Rust SDK desktop API tests including both SQLite
and PostgreSQL in-process/managed modes; and 8 TypeScript service transport tests.
Formatting, syntax and diff checks passed.

Final service ID: `ows-8fe1342add09`.
Final session: `s_42c97948ce754a83b27bace8b8bfe00d`.
Final environment: `env_4cfd7cf0f6834158bbab30942fb3fdc2` (confirmed deleted).
Qualified image: `docker.io/library/openworker-service@sha256:74299aae3bcfea7dc0151598077cfc83403cb08ef5dfa90cbfbf3aa6df41c224`.

Cleanup complete: test namespace/PVC removed, unit-test PostgreSQL stopped,
port-forwards closed, generated tokens/copied gateway credentials removed.
Only the pre-existing suspended OpenShell sandbox remains. Build caches/images are
retained for follow-up work. Final free disk is approximately 209 GiB.

Docs: [M4 quickstart](SERVICE-M4-QUICKSTART.md),
[Helm chart](../../deploy/service/chart/README.md).

Limits remain explicit: ARM64 local qualification; one namespace/default pool;
OpenShell separately provisioned; service-token login only (SSO deferred by user);
provider connections/model secrets and worker slots configured through deployment;
no live HPA/cloud-node autoscaler test and no AWS/GCP qualification yet. Older M3
API writers must be quiesced for the M4 schema/profile transition; mixed-generation
rollouts are not claimed qualified. Management UI is M5.

M4 is complete locally on `feat/openworker-service-m1`; no remote push or PR.

### M5 — management UI design preview

Created `harness/service-ui/mock/` with a dependency-free loopback preview server.
User requested a crisp Apple/Notion/Linear-inspired interface and progressive
disclosure. The mock covers Overview, Agents, Sessions, Activity, Infrastructure,
and Settings. Session traces expose steps, output, approvals, results, and usage;
configuration uses drawers and expandable options. Lightweight charts and reserved
Grafana/Langfuse settings establish the observability surface.

All records/actions are sample browser state. No service calls, infrastructure changes,
credentials, or telemetry exports are performed. Service-token authentication and
role-specific live controls are the next integration work, after design review.

Browser interaction checks passed for approval, session filtering/search, draft
creation, global search, scaling confirmation, and integration drawers. Six routes
checked at 1105px and 390px without document overflow or JS errors; desktop and mobile
screenshots reviewed. No Docker/Kubernetes resources or new package installs needed.
M5 is in progress; the management UI is not yet service-connected.


### M5 — live service integration, 27 October 2026

Implemented the separate management UI server and live browser client in
`harness/service-ui/`. The original sample-data mock remains separate for design
comparison. Live views never fall back to mock records.

Connected features:
- Service-token login with bounded, expiring in-memory sessions, HTTP-only same-site
  cookies, CSRF/Origin/Host checks, fixed service/namespace identity and strict proxy
  allowlisting. Tokens are neither browser-stored nor logged. Logout/expiry aborts
  active requests; UI restarts require login without cancelling service work.
- Authorized paginated session directory, visibility-filtered release/profile lists,
  bounded event pages and SSE. Added public reads using existing store authorization.
- Immutable release publication/activation, explicit RuntimeProfiles, task admission
  with durable operation identity, session messages, approval/question responses and
  cancellation using exact saved identity/policy bindings.
- Snapshot control receipts distinguish an accepted decision from worker application.
  Reloading retains pending decision/cancellation status and does not re-offer it.
- Desired/observed pool capacity, expected-revision scaling/configuration, service
  credential issuance/revocation and audit inspection. UI controls reflect role;
  the service enforces all writes independently.
- Lightweight charts use only loaded session records, labeled as such. No invented
  model usage totals. Grafana/Langfuse configuration remains a future integration.
- Unknown writes are never retried automatically. The UI preserves the same body/key
  for an explicit retry and warns before leaving an unresolved request. Definitive
  plain-text API rejections keep their original HTTP status.

Qualification:
- 3 browser-boundary tests: route/query checks; CSRF/cross-origin, revocation/logout,
  no mutation replay and plain-text rejection handling; independent browser expiry.
- Real PostgreSQL/Rust API/browser acceptance passed both with host Node and the
  read-only Linux UI container. Verified scoped data, SSE, approval and cancellation
  receipts after reload, deliberate lost-response/retry with identical key+body,
  role restrictions, scale/config edits, token issue/revoke, queued task submission
  and operation restoration after reload. Desktop/mobile screenshots reviewed.
- 8 PostgreSQL catalog/admission/config/pool tests, including new visibility coverage.
- 8 authorization, 9 control, and 3 ingress regression tests passed.
- 6 server-library and 5 store-library tests passed.
- 4 Helm tests passed, including an optional UI pod with no database, execution,
  gateway or Kubernetes authority and enforced HTTPS/digest inputs.
- Rust formatting, JavaScript syntax and diff checks passed.

Final local UI image:
`openworker-service-ui@sha256:a0a188577132e74e1408d50dcac2cc8aaf2cfe70a32a019c73e6d9e805b0db21`.
This is a local build, not a pushed registry artifact. No LLM key, paid model request,
cloud resources or Kubernetes namespaces were used for this UI qualification.
Fixtures exercise real persistence/API/control admission but do not apply a tool or
provision OpenShell. Existing M4 worker/provider qualification remains separate.

Packaging: pinned Node base, non-root read-only UI image, optional single-replica
Helm Deployment/Service, private-CA support. UI browser sessions are not shared across
replicas yet; a UI rollout signs users out. SSO remains deferred. A fresh combined
UI/service/OpenShell Kubernetes run and AWS/GCP are still follow-up qualification.
Run/deployment guide: [management UI](../../harness/service-ui/README.md).

Disposable API/UI processes and test UI containers cleaned up automatically; the
separate PostgreSQL fixture is removed after verification. Source and cached images
remain for follow-up work. No remote push or PR in this milestone.

### M5 complete-stack qualification — started 27 October 2026

User authorized local Kubernetes qualification of UI + workers + execution. Their
“OpenShift” wording is interpreted as the existing NVIDIA OpenShell integration;
no Red Hat OpenShift cluster is installed or claimed tested.

Plan:
1. Rebuild the Linux service from the management-UI commit using cached/offline
   dependencies; import service/UI images into the existing local kind cluster.
2. Install a dedicated namespace with PostgreSQL, API, worker, controllers, UI and
   local TLS ingress. Browser talks only to UI; UI talks to authenticated service.
3. Use the browser to publish/activate definitions, submit a task, answer an approval,
   inspect real tool outputs, and reconnect across API/UI rollouts.
4. Verify cancellation and desired/observed capacity where relevant; independently
   verify sandbox effects and durable identities. Do not retry uncertain tool actions.
5. Close sessions, delete only newly created environments, remove the test namespace
   and copied credentials, and record results/remaining limits.

Baseline: local kind `ow-server-20261011`, healthy gateway `ow-helm-gateway` in
`ow-helm-shell`, three existing suspended sandboxes across `openshell` and
`ow-helm-shell`. Approximately 207 GiB free. No cloud resources or paid LLM calls.

### M5 complete-stack qualification — passed

Fresh run passed through browser HTTPS, UI pod, API, PostgreSQL, worker/controller
and real NVIDIA OpenShell tools on local kind v1.36.4 / Linux ARM64.
Verified browser publication/activation, scoped runner submission, environment
provisioning, per-call approvals, stable approval across API restart, continued work
across UI restart/re-login, independent read proving one append, in-flight cancellation
with durable unknown-outcome history and no replay, and UI scale to two ready workers.

Initial qualification required two test corrections: read_file needed its own
approval, and a cancelled remote call correctly ended interrupted with outcome unknown.
The existing session was resumed without replaying the write. The final fresh run
passed uninterrupted in about 51 seconds. Local ingress temporary paths were fixed
for a read-only root filesystem. No harness runtime correction was necessary.

Final service: `ows-697985da05f9`.
Session: `s_25b8fa9fec9f42489f15e8b64e3a64ab`.
Environment: `env_344611edd30545a28a261533c7c69791` (confirmed deleted).
Both qualification namespaces/PVCs removed after closing/deleting their resources.
Three baseline suspended sandboxes remain. Generated secrets/copied gateway client
credentials removed. No paid inference or cloud resources; ~204 GiB disk remains.

[Qualification report](SERVICE-M5-QUALIFICATION.md) and
[sanitized evidence](evidence/service-ui-kubernetes-20261027.json).
JavaScript syntax, diff checks and all four Helm boundary tests passed.
AWS/GCP, live-model traffic, x86-64 and Red Hat OpenShift remain unqualified.
