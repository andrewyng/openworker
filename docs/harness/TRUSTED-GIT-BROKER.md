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
