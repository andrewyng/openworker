# Deploy agents and provision workspaces

OpenWorker Service now accepts immutable agent releases and RuntimeProfiles.
Applications choose an activated release, start a session, and receive a reusable
environment ID. A private controller provisions OpenShell; existing workers run
agents with their original lease, approval, streaming and recovery machinery.

## Start the service roles

Use the [M1 configuration and bootstrap](SERVICE-M1-QUICKSTART.md), then start:

```sh
openworker-server-reference --config service.json migrate
openworker-server-reference --config service.json api --node api-a --listen 127.0.0.1:8080
openworker-server-reference --config service.json service-controller
openworker-server-reference --config service.json service-worker --slots 4
```

Run each long-running command in its own process/container. Multiple controllers
and API replicas may use the same PostgreSQL schema. `service-worker --slots 4`
limits the combined concurrency across all releases/profiles in that process to
four slots; it does not allocate four slots for every release. More worker processes
increase capacity explicitly. The legacy `worker` command still handles its one
configured cohort. No Kubernetes infrastructure is created by a session request;
OpenShell must already be installed and its gateway configured.

The API persists management intents and returns receipts. Controllers perform
provisioning and verification. Workers execute models and route tools into the
sandbox. Model/provider credentials remain in worker configuration; service tokens
authenticate the caller and are not model-provider keys.

## Register a RuntimeProfile and deploy releases

These are administrator operations. Empty `allowedPrincipals` grants use only to
administrators; add the application's authenticated service principal explicitly.
In M2 the administrator publishes and activates. Separate publisher/activator roles
and the management UI remain subsequent work.

```typescript
import { Harness } from "@openworker/harness";

const service = await Harness.connect({
  url: "http://127.0.0.1:8080",
  namespace: "development",
  credentials: { tokenEnv: "OW_SERVICE_ADMIN_TOKEN" },
});

await service.registerRuntimeProfile({
  id: "team-workspace@1",
  provider: "openshell",
  cpu: 2,
  memoryMB: 2048,
  sharing: "shared",
  approvalMode: "ask",
  autoSkipQuestions: false,
  allowedPrincipals: ["course-platform"],
}, { idempotencyKey: "profile-team-v1" });
await service.activateRuntimeProfile("team-workspace@1", {
  alias: "default", idempotencyKey: "activate-team-v1",
});

await service.deployAgent({
  id: "coder@1",
  instructions: "Build the requested game. Verify your changes.",
  tools: ["read_file", "write_file", "run_shell"],
  runtimeProfiles: ["team-workspace@1"],
  defaultRuntimeProfile: "team-workspace@1",
  allowedPrincipals: ["course-platform"],
}, { idempotencyKey: "deploy-coder-v1" });
await service.activateAgent("coder@1", {
  alias: "coder", idempotencyKey: "activate-coder-v1",
});
```

Deploy `verifier@1` similarly, selecting its own instructions and narrower tool list.
The tools must exist in the operator-installed bundle. Release JSON cannot upload
an executable, register an arbitrary MCP command, supply host paths, or inject
provider credentials. Installed skills and bookkeeping features are inherited from
the trusted bundle; memory remains private to each session. Names such as
`coder@1` and `team-workspace@1` identify immutable revisions. Changing a definition
requires another revision. Repointing an alias affects future lookups; existing
agent handles and sessions retain their resolved release.

To select models, releases may set `defaultModel`, `reviewerModel` and
`modelOverrides`, using `{ connection: "primary", model: "your-model-id" }`.
Connections must already exist in the trusted worker configuration. A session's
`model` override must be explicitly allowed by the release. Auto-approve profiles
require a separate reviewer model reference; uncertain reviews still ask a human.
`bypass` skips human tool approval but does not remove OpenShell access restrictions.

## Use the release from an application

Connect using a scoped operator credential for the allowlisted principal:

```typescript
const service = await Harness.connect({
  url: process.env.OPENWORKER_URL!,
  namespace: "development",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
const coder = await service.agent("coder"); // pins coder@1 now
const coding = await coder.newSession({
  project: "learner-42-project-7",
  idempotencyKey: "project-7-coder",
});
const run = await coding.send("Build a keyboard-controlled arcade game.", {
  idempotencyKey: "project-7-build",
});
for await (const item of run.events()) {
  // Forward item.event to the UI. Use the M1 approval/control flow when needed.
}

const verifier = await service.agent("verifier@1");
const review = await verifier.newSession({
  environmentId: coding.environmentId,
  project: "learner-42-project-7",
  idempotencyKey: "project-7-verifier",
});
await review.send("Inspect the coder's files and test the game.");
```

`runtimeProfile` may select another permitted revision/alias. It is mutually
exclusive with `environmentId`. Selection order is explicit session profile,
agent default, activated profile alias `default`, then
`service.default_runtime_profile` in installation configuration. An inaccessible
default fails; access denial never triggers fallback to a weaker profile.

Sharing requires an explicitly shared profile, matching project, compatible
release/profile, and the same principal (or administrator). The environment ID
alone grants no access. Different conversations do not imply separate files or
ports: agents should coordinate changes through their workflow. Working-directory
and worktree creation overrides are not yet exposed by this service interface.

```rust,ignore
use openworker_sdk::{Harness, service::{ConnectOptions, SessionOptions}};

let service = Harness::connect(ConnectOptions::new(
    "http://127.0.0.1:8080", "development", "OW_SERVICE_TOKEN",
)).await?;
let coder = service.agent("coder").await?;
let coding = coder.new_session_with(SessionOptions {
    project: Some("learner-42-project-7".into()),
    ..Default::default()
}, Some("project-7-coder")).await?;
let verifier = service.agent("verifier@1").await?;
let review = verifier.new_session_with(SessionOptions {
    environment_id: Some(coding.environment_id().into()),
    project: Some("learner-42-project-7".into()),
    ..Default::default()
}, Some("project-7-verifier")).await?;
```

Rust also provides typed `RuntimeProfile` / `AgentRelease` and
`register_runtime_profile`, `activate_runtime_profile`, `deploy_agent`, and
`activate_agent`. The remote SDK delegates behavior to the service.

## Provisioning, disconnects and recovery

Session admission atomically saves the operation, environment intent, chosen
release/profile, owner/project, and reserved session ID before provider I/O.
`newSession()` waits until the session is usable. TypeScript accepts `waitTimeoutMs`
and `signal`: cancelling the wait does not cancel provisioning. A wait timeout
returns `operationId` and the original `idempotencyKey`. Inspect
`service.operation(id)` or repeat the same request/key to recover its original
receipt. Rust offers `admit_session()` plus `operation()` / `wait_operation()` for
applications that want to persist the admission receipt before waiting.

After a controller dispatches create, another controller may only inspect the
original object. A missing response never authorizes a replacement sandbox.
Unknown operations remain visible and reconcile against the original name,
identity label, gateway UID, accepted policy and image. If no original object can
be verified, the operation remains unknown and needs operator investigation.
Database failures stop the controller rather than replaying a possibly committed
operation. Revoked admission credentials cannot complete session admission.

`service.environment(id)` exposes the authorized profile, project, lifecycle and
active attachments. Each runtime attachment remains session-owned. Cancelling one
agent does not delete the shared environment. M2 retains environments; deletion
is a separate trusted-operator operation, subject to the existing attachment and
ownership checks. Automatic TTL cleanup is not enabled.

## Current limits

- OpenShell 0.1.3 Kubernetes gateway, operator-pinned runner image, `/sandbox`
  workspace, fixed qualified filesystem policy and network denied.
- CPU: 1–16; execution RAM: 256–65536 MiB. These are verified sandbox limits,
  separate from model context/compaction settings.
- Profile approval mode and question skipping are independent of sandbox authority.
- One service namespace/database, 64 catalog entries, 128 pending operations,
  64 retained live environments overall and 16 per principal.
- Shared service-provisioned environments are supported. Importing arbitrary
  application-owned sandboxes, provider-specific storage recovery, placement
  affinity, additional providers, expanded network/credential grants, team release
  deployment, and arbitrary working-directory overrides remain follow-ups.
- Public management uses the existing authenticated API and administrator role.
  Expiring bootstrap/login flows, finer management roles and a UI remain M4/M5.

## Local qualification

See [the progress ledger](SERVICE-PROGRESS.md) for results. The real test uses
Docker PostgreSQL, two API processes, two controllers, one bounded worker and real
OpenShell tool execution on local Kubernetes. A scripted model makes the sequence
repeatable; this does not claim live-model quality evaluation.

```sh
npm --prefix harness/typescript-sdk run build
# Set OW_PG_TEST_URL and the existing gateway's OW_PROBE_* variables as in M1.
cargo test --manifest-path harness/Cargo.toml -p openworker-server-reference \
  --features openshell-test --test service_v2 -- --ignored --nocapture
```
