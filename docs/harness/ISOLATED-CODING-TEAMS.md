# Isolated coding teams

A builder and verifier can work in separate OpenShell sandboxes without GitHub
credentials. Source and submitted commits cross the boundary as immutable Git
bundles through the configured artifact store. The harness and controllers remain
outside the tool sandboxes.

```text
Lead sandbox             Artifact store             Builder sandbox
pinned source  --------> initial bundle ----------> independent checkout
                                                    edit, test, commit
                         submitted bundle <------- team_submit_code
                                |
                                +-----------------> Verifier sandbox
                                |                   exact commit + own .git
                                |                   test, report verification
                                v
Lead: receive in separate checkout
      inspect reports + source
      integrate(expected HEAD) -> source branch CAS
```

## Configure the service

The operator installs a coding tool bundle, artifact backend, activated releases
and RuntimeProfiles. The application supplies the original repository identity
and exact base SHA; that checkout must already exist in the lead environment.
Source staging exports only committed data. Private remote checkout/publication
is a separate optional integration, not implied by this preset.

Use a shared-capable lead profile and an explicit child profile inside the lead's
delegation ceiling. A new child profile supplies exact supported resource and
network/credential grants. No model-provided server URL or credential is accepted.
The service derives its artifact binding from operator configuration.

## TypeScript

These snippets assume `service` is connected and `profile` is an activated
RuntimeProfile. `baseCommit` is an exact commit in the existing lead checkout.
The profile's approval mode must match the agent definitions; configure approved
model connections separately.

```typescript
import {
  isolatedCodingServiceTeam, codingRelease, runtimeProfileGrants,
} from "@openworker/harness";

const grants = runtimeProfileGrants(profile);
const preset = isolatedCodingServiceTeam(
  "game",
  { repository: "my-game", directory: "source", objectFormat: "sha1", commit: baseCommit },
  { mode: "new", profile: profile.id, ...grants },
  { tools: codingTools, placements: ["new"], profiles: { [profile.id]: grants } },
  "1",
);
for (const agent of [preset.lead, preset.builder, preset.verifier]) {
  await service.deployAgent(codingRelease(agent, "1", profile));
  await service.activateAgent(`${agent.name}@1`);
}
const agent = await service.agent("game-lead@1");
const lead = await agent.newSession({ environmentId: sourceEnvironmentId, project: "game" });
// Create the team before the lead's first turn.
const team = await lead.createTeam(preset.team);
const run = await lead.send("Build the requested game and verify the submitted commit.");
for await (const event of run.events()) renderActivity(event);
const page = await team.submissions({ limit: 20 });
```

The lead can spawn multiple instances of the registered builder type. A verifier
must name a submission, so it cannot silently check only the original base:

```typescript
await team.spawn("verify-1", "game-verifier", "verifier-one", "Verify this exact revision", {
  source: { kind: "submission", id: submissionId },
});
```

The first argument is the operation's idempotency key; `verifier-one` is the
instance ID. Keep them stable when reconnecting after an uncertain response.

## Rust

Rust uses the same service-backed contract and the same versioned preset data.
The builder and verifier are configuration over the existing agent/team runtime.
The source/delegation values below are application-owned validated configuration.

```rust,ignore
use openworker_sdk::agents::coding;

let preset = coding::isolated_service(
    "game", source, child_sandbox, delegation, "1",
)?;
// Deploy/activate coding::service_release(...) for preset.agents(), then
// create the lead session in the environment containing the pinned source.
let team = lead.create_team(preset.team).await?;
let run = lead.send("Build and verify the requested game", Some("build-1")).await?;
let submissions = team.submissions(None, 20).await?;
```

See the executable [Rust service fixture](../../harness/server-reference/tests/support/service_handoff.rs)
and [TypeScript continuation](../../harness/typescript-sdk/test/service-v2-live.mjs)
for exact service setup, turn streaming, source selection and read-back assertions.
The artifact-first isolated preset currently targets the PostgreSQL service with
OpenShell; it does not turn a local SQLite process into an isolated provider.
Existing single-agent/shared presets retain SQLite/PostgreSQL and embedded/managed
SDK support.

## Agent-visible workflow

| Tool | Effect |
| --- | --- |
| `team_submit_code` | Persist an export request for the builder's exact commit. Returns an ID and pending/ready/unknown status. |
| `team_list_submissions` | Read a bounded catalog, including manifest, reports and reception/integration state. |
| `team_report_verification` | Attribute a report to a member that acknowledged this exact submission. |
| `team_receive_submission` | Lead receives a separate independent checkout; its original workspace does not change. |
| `team_integrate_submission` | Lead requests a clean fast-forward with an explicit expected HEAD. |

Export, reception and integration use the existing approval modes. A report is
an agent's claim, not proof that tests passed or permission to merge. A stale,
dirty or divergent source rejects. Integration never resolves conflicts, resets
user changes, pushes a branch or creates a pull request automatically.

The controller persists dispatch intent before I/O. A lost reply causes inspection
of the original operation, not another export, checkout or integration. Git's
index/files update and branch CAS are separate operations; partial outcomes remain
unknown. Keep the original work for inspection. Retirement/cancellation race
qualification and mixed-team recovery are tracked in C7; unpublished work is not
automatically deleted when a member closes.

[Design and implementation](CODING-HANDOFF-IMPLEMENTATION.md) ·
[Test catalog](CODING-AGENT-PRESETS-TESTING.md) ·
[Progress and actual qualification](CODING-AGENT-PRESETS-PLAN.md) ·
[Optional sandbox Git access](OPENSHELL-GIT-ACCESS.md)
