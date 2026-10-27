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
