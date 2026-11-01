# Coding agents

The `coding-v1` presets provide instructions, first-party coding tools and a
durable plan. They use the same agents, sessions, approvals and team runtime as
your own definitions. A single coding agent is the default entry point; choosing
one does not start workers or give it GitHub access.

These coding/process workflows are qualified on Linux and OpenShell. Native
macOS/Windows process tools are a separate qualification; the desktop Game Builder
can continue using application-provided tools. See the
[test catalog](CODING-AGENT-PRESETS-TESTING.md) and
[journal](CODING-AGENT-PRESETS-PLAN.md) for actual test evidence.

For ASCII diagrams comparing shared worktrees, offline child source transfer,
artifact handoff and the upcoming GitHub modes, see
[workspace and GitHub scenarios](CODING-WORKSPACE-SCENARIOS.md).

## One agent

```rust,no_run
use openworker_sdk::agents::{coding, Harness};
# async fn example() -> Result<(), Box<dyn std::error::Error>> {
let harness = Harness::builder()
    .agent(coding::single("coder").model("OpenAI", "your-model"))
    .start().await?;
// Configure provider credentials separately using set_model_provider.
let session = harness.session("coder", "/your/project").await?;
let result = session.run("Build a small game and run its tests.").await?;
// Handle approval interruptions using the ordinary session API.
println!("{:?}", result.status);
harness.shutdown().await?;
# Ok(()) }
```

```typescript
import { Harness, codingAgent } from "@openworker/harness";

const harness = await Harness.start();
// Configure the provider/key separately using setModelProvider.
const coder = harness.createAgent(codingAgent("coder", {
  defaultModel: { provider: "OpenAI", name: "your-model" },
}));
const session = await coder.newSession({ workspace: "/your/project" });
const run = await session.send("Build a small game and run its tests.");
for await (const event of run.events()) {
  // Forward content, activity and approval requests to your UI.
}
```

Use ordinary Agent options to customize tools, instructions, default models,
budgets and approval handling. Ask remains the default. Auto-approval requires
its separately configured reviewer; bypass is an explicit application choice.
Model defaults can still be overridden for a new session. Credentials never belong
in a preset. Optional helpers use the existing team/delegation API and need an
app-owned ceiling; they are not implicitly enabled by this preset.

## A shared coding team

Prepare a repository inside the shared project, for example `/project/source`,
and obtain its trusted base commit SHA. The repository and commit must already
exist in the execution environment. Preset construction does not clone, fetch,
switch branches or grant credentials.

```rust,no_run
use openworker_sdk::agents::{coding, Harness};
# async fn example(base_sha: &str) -> Result<(), Box<dyn std::error::Error>> {
let preset = coding::shared("game", "source", base_sha)?;
let mut builder = Harness::builder();
for agent in preset.agents() { builder = builder.agent(agent); }
let harness = builder.start().await?;
let team = harness.new_team(preset.lead.id(), "/project", preset.team).await?;
let result = team.lead().run("Build the UI, then verify the submitted commit.").await?;
println!("{:?}", result.status);
harness.shutdown().await?;
# Ok(()) }
```

```typescript
import { Harness, sharedCodingTeam } from "@openworker/harness";

const preset = sharedCodingTeam("game", {
  repository: "source", base: baseCommitSha,
}, {
  builder: { defaultModel: { provider: "Local", name: "your-model" } },
});
const harness = await Harness.start();
await Promise.all([preset.lead, preset.builder, preset.verifier]
  .map(definition => harness.createAgent(definition).ready()));
const team = await harness.newTeam(preset.lead.name, {
  workspace: "/project", ...preset.team,
});
const run = await team.lead.send("Build the UI, then verify the submitted commit.");
for await (const event of run.events()) { /* update your UI */ }
```

The lead starts alone with registered builder/verifier types. A builder spawned
without a workspace selection receives its own worktree and branch from the
app-pinned SHA. The runtime resolves the path before reservation and returns it
in the spawn result. Names include the team identity, so two teams do not reuse
each other's branches. Explicit directory/worktree selections retain their normal
meaning; `mode: team` selects these defaults for configured builder types.

The child cwd and file-tool scope are its worktree. Git inspection admits only
its recorded shared metadata. Processes still share the sandbox's aggregate
authority. The lead uses the supplied repository's existing main worktree for
integration; it does not automatically switch that branch or merge submissions.

A verifier receives the submitted SHA and an explicit directory selection for
the builder's worktree. It must check that SHA before testing and report if the
worktree changed. Tests/shell commands can write files; the verifier role is not
a read-only kernel sandbox. The lead explicitly reviews and integrates verified
changes, handling base movement and conflicts.

## Use the presets with a service

Install the first-party coding bundle with `planning: true`. The bundled
`deploy/service/coding-bundle` enables it. Register/activate the RuntimeProfile
and releases through the ordinary management API. The SDK helper converts role
definitions into release data; it does not deploy anything on construction.

```rust,no_run
use openworker_sdk::agents::coding;
# fn example(profile: &openworker_sdk::service::RuntimeProfile, base_sha: &str)
# -> Result<(), Box<dyn std::error::Error>> {
let preset = coding::shared("game", "source", base_sha)?;
for agent in preset.agents() {
    let release = coding::service_release(&agent, "1", profile)?;
    // service.deploy_agent(&release, key).await?; then activate it.
}
let team_options = preset.service_team("1");
// An admitted game-lead@1 session in the shared environment:
// lead.create_team(team_options).await?;
# Ok(()) }
```

```typescript
import { codingRelease, codingServiceTeam } from "@openworker/harness";

for (const agent of [preset.lead, preset.builder, preset.verifier]) {
  const release = codingRelease(agent, "1", profile);
  await service.deployAgent(release);
  await service.activateAgent(release.id);
}
// After admitting game-lead@1 in the shared RuntimeProfile/environment:
const team = await lead.createTeam(codingServiceTeam(preset, "1"));
```

Service approvals and sandbox settings belong to the activated RuntimeProfile.
The conversion checks matching approval/question modes and rejects local-only
policies or unsupported capabilities instead of ignoring them. Model and reviewer
references stay distinct; keys remain service configuration. For a bypass test
profile, explicitly set each role's approvals and question setting to match it.

Shared worktrees require a qualified adapter (local sandbox-off SDK or the
OpenShell service). Unsupported adapters reject; they do not quietly use the
lead's directory. Separate child placement is described below. Independent source
staging, the isolated coding preset, artifact handoff and constrained GitHub
publication are tracked separately in C4–C7.


## Choose a child's sandbox in the OpenShell service

The application's activated releases and RuntimeProfiles define the available
provider settings. Profiles have immutable IDs such as `small@1`. Source and child
releases must permit the target profile, its resources must fit the lead's
profile, and approval/question behavior must match. The current adapter supports
CPU/memory profiles with the existing deny-network policy; nonempty named file,
network or credential grants reject until that provider support is qualified.

```typescript
const team = await lead.createTeam({
  agentTypes: { builder: "builder@1", verifier: "verifier@1" },
  delegation: {
    tools: ["coding.read_file"], // permitted tools for private custom types
    placements: ["team", "member", "new"],
    profiles: { "small@1": { cpu: 1, memoryMB: 512 } },
  },
});
await team.spawn("build-ui", "builder", "ui", "Build your assigned part.", {
  sandbox: { mode: "new", profile: "small@1", cpu: 1, memoryMB: 512 },
});
// After ui is admitted in a shareable sandbox:
await team.spawn("verify-ui", "verifier", "check-ui", "Verify ui's exact submission.", {
  sandbox: { mode: "member", agentId: "ui" },
});
// Omitting sandbox retains the team's original environment.
```

Rust uses the same `AgentTeamOptions.delegation` and
`ChildOptions.sandbox` contracts. The lead can request these choices through
`team_spawn`; an approved private type may also set its default sandbox. A custom
instance cannot enlarge or switch that exact type's approved envelope.

A new sandbox starts at its own `/sandbox`. It does not mount the lead's files or
Git metadata. Directory selection uses an **existing** directory in the selected
sandbox; it does not create or import source. Member selection can wait for an
existing reservation's provisioning, and only binds an available shareable
original environment. It cannot name a future, retired or unrelated instance.
Use C4's source/artifact workflow once qualified for isolated coding checkouts.

The trusted harness/controller calls the gateway outside the sandbox. Allocation
and quota ownership commit before create. Recovery inspects the same original
resource; it never recreates after a lost response. Unknown outcomes remain
visible in events and the lead inbox without authorizing a retry. Child
admission rechecks current credentials, profiles and original binding.

Retirement closes the child's admission and preserves its work. Other members'
active/reserved references prevent deletion. Owned resources remain visible and
consume quota until explicit confirmed cleanup. Provider-local files last for
that sandbox's lifetime; an independently persistent volume is not implied.
Local sandbox-off SDK adapters reject New/Member placement because they have no
qualified remote provisioner; they do not execute the request on the host.
