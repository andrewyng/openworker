# Trusted repository staging and publication

C5 implementation design. The default remains artifact-only handoff. This optional
server-side adapter holds GitHub credentials outside every agent sandbox. It does
not replace the separately qualified OpenShell Git CLI mode.

```text
Operator grant + secret reference           Agent sandbox
         |                                  commit locally
         v                                  team_submit_code
Trusted broker <---- approved intent <----- immutable submitted artifact
  exact repo/ref                            (no token/remote URL)
  read-only fetch or constrained publish
         |
         +---- GitHub HTTPS
```

## Authority and identity

An operator installs versioned repository grants. A grant names exact GitHub
repositories, readable refs, writable branch prefixes and a credential environment
reference, with optional expiry/revocation. It contains no credential value.
RuntimeProfile selects a grant by name; a child can keep that exact grant or choose
none. Different names are not presumed permission subsets. API and worker pin the
same nonsecret installation/descriptor; rotating a secret does not rewrite policy.

App-owned coding configuration can request initial source from a named readable
ref and exact commit. Model publication names a same-team immutable submission,
branch and expected remote tip. Neither path accepts a URL, host path, raw token,
arbitrary Git argument, PR operation or broader repository scope.

Read and publication permissions are independent. Tool approval does not waive
repository, branch, source/manifest, profile or credential checks. Model reports
are not evidence of authorization or a successful publication.

## Trusted native adapter

Use an operator-installed Git executable in an operation-owned directory outside
agent workspaces. Clear inherited configuration and environment; disable prompts,
hooks, helpers, URL rewriting, submodules, external filters and replacement objects.
Authenticate only to the exact configured HTTPS origin with an ephemeral header.
Do not expose command stderr, environment, headers or keys in errors/events.
Bound output, object transfer, operation time, disk use and subprocess lifetime.

Remote staging fetches the pinned permitted ref, checks the requested commit and
produces the same bounded, self-contained Git bundle used by C4. The artifact
store verifies it. ToolRunner imports that bundle before the lead or child may use
that checkout; no GitHub credential enters ToolRunner. Existing workspace-source
setup stays supported and does not silently fetch a remote.

Publication imports only verified artifact bytes into the trusted broker's own
Git metadata. It validates repository/base/commit and ancestry. New branches use
create-only expected absence. Updating an allowed branch requires an explicit
expected old tip and fast-forward ancestry, followed by a remote compare-and-swap.
There is no non-fast-forward update, implicit force-push, PR creation or merge.

## Durable operations

Persist intent, policy pin, original artifact and first-dispatch bit before I/O.
Use the existing PostgreSQL/controller/inbox/event framework. Different model call
IDs cannot repeat the same intended publication. A lost push response means the
outcome is unknown; recovery reads the original remote ref only. Observing the
submitted tip can acknowledge the desired state, without claiming causality.
An unchanged/different tip is not proof a prior mutation never happened and does
not authorize a retry. Credentials expiring or being revoked never triggers a
fallback to agent CLI credentials. Preserve original evidence and work.

## Build order and qualification

1. Implement an optional trusted Git adapter, scope validation and real controlled
   remote tests: pinned read, create-only push, fast-forward CAS, foreign scope,
   stale/dirty/divergent inputs, lost response and expired/revoked credentials.
2. Wire operator grants and RuntimeProfiles, durable publication intents, approval
   gates, controller execution, catalog and Rust/TypeScript SDK representation.
3. Wire remote source staging into the existing artifact/import/admission path;
   qualify private source → isolated builder → verifier → trusted publication.
4. Run combined local PostgreSQL + Kubernetes/OpenShell qualification, document
   cleanup, deployment dependencies and remaining platform limits, then merge.

Keep Git broker execution optional in the service package. It must not add Git,
cloud SDKs or another runtime dependency to the core harness deliverable. Local
qualification uses disposable Git remotes and synthetic keys. A live GitHub write
requires a separately authorized disposable repository; no such write is currently
part of the test scope.

[Milestones and journal](CODING-AGENT-PRESETS-PLAN.md) ·
[Artifact handoff](CODING-HANDOFF-IMPLEMENTATION.md) ·
[OpenShell CLI access](OPENSHELL-GIT-ACCESS.md) ·
[Workspace illustrations](CODING-WORKSPACE-SCENARIOS.md)

## Operator and application setup

Build the optional Linux service image (the ordinary runtime/SDK does not gain a
Git executable or credential provider):

```sh
docker build -f deploy/service/Dockerfile --target git-broker-runtime \
  -t openworker-service:trusted-git .
# Or a native trusted service build:
cargo build --manifest-path harness/Cargo.toml --release \
  -p openworker-server-reference --features trusted-git
```

Install the same **nonsecret** registration on API and controller/worker nodes:

```json
{
  "repository_grants": {
    "game-github@1": {
      "credential_env": "GAME_REPOSITORY_TOKEN",
      "repositories": {
        "game": {
          "github": "your-org/your-game",
          "read_refs": ["refs/heads/main"],
          "write_prefixes": ["agents/"]
        }
      }
    }
  }
}
```

Select `repositoryAccess: "game-github@1"` in the activated lead RuntimeProfile.
A narrower child profile can omit it. The installed/release tool list must also
select `github_broker.publish_code` for any agent allowed to request publication.
The default coding presets do **not** select it. API descriptors expose no key;
only the trusted controller's named environment contains the repository token.
Rotating that token does not change the pinned nonsecret grant. Changing repository
scope requires a new grant/profile version and new compatible session.

To import private source, pass an app-owned ordinary ref **and** exact commit:

```typescript
const source = {
  repository: "game", directory: "source", objectFormat: "sha1" as const,
  remoteRef: "refs/heads/main", commit: pinnedCommit,
};
// Pass source to isolatedCodingServiceTeam(...).
const team = await lead.createTeam(preset.team);
const snapshot = await team.snapshot();
// snapshot.source_setup.status: pending/exporting/uploading/ready/unknown/blocked.
// Wait for ready using your normal event/status handling before lead.send(...).
```

Rust uses `openworker_sdk::service::CodingSource` with `remote_ref` and
`object_format` (`GitObjectFormat::Sha1` or `Sha256`).
Omitting `remote_ref` preserves existing-workspace export. The destination must
be a fresh relative directory; `.` is deliberately rejected for remote setup.
The lead stays rooted at its assigned project directory; its imported source is
`source/`. Imported repositories have their own Git metadata and the ordinary
local branch `openworker/import`, enabling later explicit expected-head integration.
No Git remote or credential is installed in that checkout.

```text
private GitHub repository
    | permitted ref + pinned commit (trusted broker only)
    v
object-only trusted staging cache -- verified immutable bundle --> artifact store
                                                               |
                           ToolRunner <--- authenticated bytes --+
                              |
                  fresh lead source + independent child checkouts
                              |
                    model commit -> team_submit_code
                              |
                 verifier uses exact submission ID
                              |
       selected native github_broker.publish_code (ordinary tool approval)
                              |
           durable publication -> original remote branch CAS
```

The native callback returns **pending**, not a completed push. Watch
`team.submissions()` and `team_publication` events for the original operation.
Create-only means `expected_head: null`; updates require the exact current tip and
fast-forward ancestry. After an unknown outcome, recovery only reads that original
ref—even on another node with no old cache. It never repeats the push.

Keep the broker's private state directory durable until source artifact publication.
Losing an unacknowledged source export cache leaves setup unknown; a replacement
node cannot silently generate different bytes under the original artifact ID.
Once verified artifact bytes are published, lead import/recovery can proceed from
that store without the old cache. Preparation and checkout each have durable
first-dispatch bits. A lost checkout reply permits inspection only. Source setup
blocks first-turn submission and automatic inbox wakes until the lead checkout is
acknowledged. Original source/inbox/events survive worker replacement.

## Local qualification and boundaries

`trusted-git-test` / the broker `fixture` feature permit explicit disposable file
or loopback HTTPS fixtures. Deployment images never enable those features.
The HTTPS fixture uses a self-signed test CA with verification enabled and a
synthetic credential; production has no endpoint or TLS-verification override.
Real GitHub write qualification needs a separately authorized repository.

OpenShell 0.1.3 retains successful durable mutation receipts for 24 hours and caps
each caller at 1,000 retained admissions, including successes. Repeated exec/setup
qualification reached that upstream limit. Provision an isolated gateway for
large acceptance runs, retain original unknown-operation evidence and plan caller
capacity with the OpenShell operator. Do not clear receipts or disable request
identity to evade it. See the pinned upstream
[mutation admission implementation](https://github.com/NVIDIA/OpenShell/blob/v0.1.3/crates/openshell-server/src/grpc/mutation_replay.rs).
