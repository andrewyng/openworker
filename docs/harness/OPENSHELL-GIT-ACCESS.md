# Git access through OpenShell

The service supports opt-in Git HTTPS access from its Linux ToolRunner. The
operator installs a named Git grant; an application selects it through an
immutable RuntimeProfile. The sandbox receives OpenShell placeholders. Real Git
credentials stay in OpenShell's trusted credential path.

This is separate from [offline source/artifact handoff](CODING-WORKSPACE-SCENARIOS.md):
artifact imports need no GitHub network access or GitHub credential. Enabling a
Git grant is an explicit choice, not a fallback after a failed artifact operation.

```text
Operator setup                         Application / SDK
  OpenShell provider/profile pin         RuntimeProfile.gitAccess = "github-read@1"
  exact helper host + repositories                 |
               |                                  v
               +---- named grant registry ---- immutable profile + release
                                                  |
                                       admit session / child placement
                                                  |
                                       original environment identity
                                                  |
  +---------------- OpenShell sandbox ------------v---------+
  | Rust ToolRunner -> shell -> Git -> Rust credential helper |
  |                    placeholder only                     |
  +---------------------------|-----------------------------+
                              v
                   OpenShell inspected HTTPS proxy
                   binary/request + credential binding checks
                   placeholder replaced with real credential
                              |
                              v
                    authorized Git HTTPS remote
```

## Operator setup

Install/configure the credential provider in OpenShell first. Review its allowed
endpoints, request methods, executable ancestry and credential scope. Read-only
clone/fetch and publication are different grants. The standard GitHub profile
does not implicitly authorize push. Keep TLS inspection and verification enabled.

Pin the existing original provider/profile with the native adapter's
`OpenShell::pin_credential_provider(name)`. Save the returned **non-secret** pin,
not just its name. It contains `name`, `provider_id`, `profile_id`,
`profile_workspace` and `profile_digest`, plus `configuration_digest` when its
non-secret configuration is nonempty. The adapter rechecks the original
provider/profile before create, binding and new execution attachment.

Put the pin and helper scope in the service configuration:

```json
{
  "openshell": {
    "git_grants": {
      "github-read@1": {
        "provider": {
          "name": "github-read",
          "provider_id": "<original ID returned by pinning>",
          "profile_id": "github",
          "profile_workspace": "",
          "profile_digest": "<64-character digest returned by pinning>"
        },
        "scope": {
          "host": "github.com",
          "repositories": ["your-org/your-repo"]
        }
      }
    }
  }
}
```

Merge this into the existing configuration; retain its gateway, image, mTLS,
workspace and other settings. Use a versioned grant name. The pin shown above is
a shape example: use actual returned IDs/digest. No token, refresh material or
private key belongs in this registry. The Helm chart and local-container
configuration preserve it without selecting a grant by default.

The registry is part of the service installation identity. Replacing a provider,
editing its pinned profile, changing helper scope or changing the installed
registry cannot silently modify a running definition. Deploy newly reviewed
definitions/configuration for such changes. Ordinary credential rotation within
the original provider does not require replacing its policy pin.

The pinned [OpenShell provider protocol](https://github.com/NVIDIA/OpenShell/blob/v0.1.3/proto/datamodel.proto)
separates credentials from non-secret configuration. Configuration changes are
detected without decoding credential maps or persisting configuration values.

The helper scope limits where the helper returns a placeholder. It is **not** a
replacement for OpenShell's actual network/credential policy. Shell-capable code
can construct its own HTTPS requests; configure the OpenShell profile and upstream
credential restrictions accordingly. A placeholder is a capability shared within
its sandbox, not an agent-private secret.

## TypeScript service SDK

Use the same profile registration, activation and release deployment APIs as any
other service agent. The new field is `gitAccess`:

```typescript
import { Harness, runtimeProfileGrants } from "@openworker/harness";

// Connect using the existing service-token configuration.
const service = await Harness.connect(serviceOptions);
const profile = {
  id: "coding-github@1", provider: "openshell" as const,
  cpu: 1, memoryMB: 768, sharing: "shared" as const,
  gitAccess: "github-read@1",
};
await service.registerRuntimeProfile(profile, { idempotencyKey: "coding-profile-v1" });
await service.activateRuntimeProfile(profile.id, { idempotencyKey: "activate-profile-v1" });
await service.deployAgent({
  id: "coder@1", instructions: "Work on the requested repository.",
  tools: ["run_shell", "read_file", "write_file"],
  runtimeProfiles: [profile.id], defaultRuntimeProfile: profile.id,
}, { idempotencyKey: "coder-release-v1" });
await service.activateAgent("coder@1", { idempotencyKey: "activate-coder-v1" });

const session = await (await service.agent("coder@1")).newSession({
  project: "my-project", idempotencyKey: "project-session",
});
const run = await session.send("Clone the authorized repository and inspect it.");
for await (const event of run.events()) { /* render progress */ }
const result = await run.result();

// Exact named sets for an approved child profile's delegation/New placement:
const grants = runtimeProfileGrants(profile);
// {files: [], network: ["git:github-read@1"],
//  credentials: ["git:github-read@1"], cpu: 1, memoryMB: 768}
```

Profile/release registration needs service administrator authority; applications
with an operator token use already activated definitions. Approval mode remains
independent. The SDK does not get the GitHub key or launch OpenShell itself.

## Rust service SDK

`RuntimeProfile` exposes the same optional `git_access` field and a
`delegated_grants()` helper. JSON uses `gitAccess` in both SDKs.

```rust,ignore
use openworker_sdk::service::RuntimeProfile;

let profile: RuntimeProfile = serde_json::from_value(serde_json::json!({
    "id": "coding-github@1", "provider": "openshell",
    "cpu": 1, "memoryMB": 768, "sharing": "shared",
    "gitAccess": "github-read@1"
}))?;
service.register_runtime_profile(&profile, "coding-profile-v1").await?;
service.activate_runtime_profile(&profile.id, None, "activate-profile-v1").await?;
let grants = profile.delegated_grants();
// Pass these exact network/credential/resource sets in team delegation and
// a New sandbox choice, using the existing team APIs.
```

The runtime supplies the credential helper flags only when launching the installed
coding runner in the original granted environment. It does not rewrite agent
bundles or infer permission from a tool's label. Existing profiles without
`gitAccess` keep credential-free behavior.

## Children, sharing and lifecycle

Configure the team with `session.createTeam()` / `session.create_team()` before
starting its lead's first turn, as required by the existing team API.

A child can use its lead's exact named grant or select a credential-free profile
for a new sandbox. Cross-grant delegation rejects: a different provider name or
"read" label is not proof that its authority is a subset. Exact requested
network/credential sets must match the selected RuntimeProfile, and its CPU,
memory, approval and question settings remain bounded by the lead. Arbitrary
file/network/credential grant names still reject.

The helper scope contributes to the environment profile digest. Changing it or
substituting an environment fails identity checks. Selecting a credential-free
profile clears inherited launch configuration. Credentials belong to the sandbox;
agents sharing a granted sandbox share that capability. A separate credential-free
sandbox is required when a child must not have it.

New tool connections supply their current OpenShell reference to new commands,
including commands launched by an existing sandbox-local process manager. Real
keys are never cached in the manager or saved in Git config. Expiry and detach
are enforced by OpenShell; after an administrative change, check acknowledgment
of the original supervisor revision, not just that the gateway saved the change.
Original-identity cleanup remains possible after detach. No lost operation reply
authorizes replay or provisioning a replacement sandbox.

Git over SSH and `gh` are not qualified by this implementation. HTTP policy does
not inspect branch names inside Git packs: use upstream branch protections or the
trusted publisher when branch restrictions are needed. Untracked/ignored files,
repository hooks and test execution remain subject to their separate policies.

## Qualification

The [grouped test catalog](CODING-AGENT-PRESETS-TESTING.md) and
[progress ledger](CODING-AGENT-PRESETS-PLAN.md) record actual completed runs.
The [local fixture](../../harness/runtime/tests/fixtures/openshell-git/README.md)
uses a synthetic-token Git HTTPS remote, verified TLS and a real OpenShell gateway.
No real GitHub credential or paid model is needed. The extended service suite
enables this layer with `OW_GIT_PROVIDER` and `OW_GIT_HOST` alongside its existing
`OW_PG_TEST_URL` / `OW_PROBE_*` variables. It covers SDK registration, real worker
Git traffic, same-grant and credential-free children, original-session restart
and cleanup. The ledger distinguishes an implementation from a completed live run.
