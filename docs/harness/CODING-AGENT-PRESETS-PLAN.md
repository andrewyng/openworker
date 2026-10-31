# Coding agent presets: implementation sequence and progress

Status: sequence prepared for review; implementation has not started under these
milestone IDs. Canonical feature branch: `openworker-harness-sdk`. Use a feature
branch for each slice and merge only verified changes. No public-main merge or
cloud provisioning is part of this plan.

Design: [coding presets, defaults and publication](CODING-AGENT-PRESETS.md).
Underlying primitives: [team design](TEAM-TYPES-AND-SANDBOXES.md) and
[team implementation ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md).

This is the current coding-feature sequence. IDs **C1–C7** distinguish it from
the earlier M1–M5 team-foundation milestones and their completed slices. Do not
rebuild approved types, inboxes, approval modes, retirement or the agent loop.
Extend the same primitives through the remaining deployment adapters.

## Defaults and implementation rules

- Default entry point: a single coding agent, with no automatically started children.
- Default team preset: shared sandbox, separate builder worktrees.
- Each child's worktree/checkout is its working directory and ordinary file-tool
  scope. Shared Git metadata access is deliberate and narrowly validated; cwd is
  not kernel containment for shell commands.
- Isolated workers have independent checkouts and their own Git metadata.
- Default isolated handoff: artifacts. GitHub publication is opt-in; when enabled,
  prefer the trusted publishing tool. Sandbox CLI credentials are an explicit mode.
- Presets are versioned configuration over the existing runtime. Keep optional
  publishing integrations out of the core dependency path where possible.
- All choices stay within application-owned delegation and provider enforcement.
  Unsupported restrictions or modes reject instead of falling back.

## Milestones

| ID | Deliverable | Dependency | Status |
| --- | --- | --- | --- |
| C1 | Child directories and Git worktrees inside the shared sandbox | Existing local worktree receipts and shared service teams | Pending |
| C2 | Single coding-agent and shared-team presets | C1 | Pending |
| C3 | Delegated child sandbox placement and lifecycle | Existing type/delegation/lifecycle contracts | Pending |
| C4 | Isolated coding-team preset and artifact handoff | C2, C3 | Pending |
| C5 | Harness-managed GitHub checkout/publication integration | C4 handoff contract | Pending |
| C6 | Explicit sandbox Git CLI credential mode | C3, C5 policy/credential contracts | Pending |
| C7 | Combined parallel, mixed-placement and recovery qualification | C1–C6 | Pending |

Execute in this numbered order. C3 builds on generic team placement rather than
adding a coding-only sandbox provisioner. Each milestone includes tests and ledger
updates; C7 expands coverage across the combined system rather than postponing
verification until the end.

### C1 — Child directories and Git worktrees inside the shared sandbox

Extend the tested local directory/worktree preparation path through the service
and OpenShell ToolRunner. The trusted adapter prepares a distinct child worktree
at a pinned repository/base revision and persists the selected working directory
and receipt. The execution environment keeps its original identity and enforcement
root; the child's cwd and file-tool scope must not be confused with that root.

Record the worktree's repository/common-metadata relationship and original path
identities. Adapt trusted Git inspection to admit that recorded relationship
without broadening ordinary file-tool access. A lost preparation response permits
inspection of the original operation, not another `git worktree add`.

Acceptance:

- Two builders edit separate worktrees in one sandbox; their relative paths and
  shell cwd resolve to their own directories.
- File tools reject sibling writes, traversal and symlink escapes. Authorized Git
  operations work with the registered shared metadata; replaced/arbitrary metadata
  pointers reject. Shell access remains bounded by the actual shared sandbox.
- The lead can inspect builder commits; a restart or lost acknowledgement preserves
  the original directory, branch and environment without replaying preparation.
- Existing same-directory service sessions retain their supported behavior.

### C2 — Single coding-agent and shared-team presets

Provide versioned SDK presets with customizable prompts, tools, models, budgets
and delegation. Start the single agent without children. With delegation enabled,
it may create helpers through the same approved-type/team mechanisms. The shared
team preset supplies lead, builder and verifier roles and uses C1 worktrees.

Finalize the small typed construction API in Rust and TypeScript against executable
examples. Model defaults remain overridable per session; reviewers stay separately
configured. Credentials remain outside preset bundles. No separate agent loop.

Acceptance:

- A single agent builds a small web app; optional helper creation respects the
  supplied ceiling and configured approval mode.
- A lead coordinates builders, a verifier checks the exact submitted commit, and
  explicit integration handles success and conflicts.
- Rust in-process/managed and TypeScript managed/service entry points expose the
  supported preset behavior with storage parity where those modes apply.
- Spawning never implicitly commits, merges, pushes or grants GitHub access.

### C3 — Delegated child sandbox placement and lifecycle

Implement Team/Member/New selection through the existing trusted execution-provider
adapter. New environments use app-authorized profiles and resolved restrictions.
Every requested file/network/credential/resource restriction must be supported and
enforced by the selected provider; otherwise reject it. Definitions cannot enlarge
the lead's delegation ceiling. Sharing requires compatible resolved enforcement,
not matching labels or narrower tool lists alone.

Persist provisioning intent and original provider/storage identities before I/O.
Reconcile unknown outcomes through reads of the original resource. Track ownership,
references, quotas and retention so retirement cannot delete an environment still
in use or silently destroy retained work. Do not claim independently persistent
storage when the provider only preserves files for the lifetime of its sandbox.

Acceptance:

- Shared, reused-member and new isolated placements behave as requested.
- Excess grants/resources, incompatible sharing and unsupported constraints reject.
- Controller failure, lost create/delete replies, cancellation and retirement do
  not create replacements, leak unaccounted capacity or delete shared resources.
- Original provider identity, access and lifecycle remain visible through durable
  state/events. Keep the harness/provisioner outside OpenShell for this sequence.

### C4 — Isolated coding-team preset and artifact handoff

Stage an independent checkout/source snapshot at a recorded base revision in each
worker environment. Do not mount a linked worktree dependent on the lead's Git
metadata. Implement one durable handoff record for repository/base, submitted
revision, artifact digest/reference and verification evidence. Use existing artifact
storage and authorized metadata APIs rather than a second untracked transfer path.

Builders export patches or Git bundles. Verifiers inspect the exact submission;
the lead explicitly applies/integrates it and handles a changed integration base.
Validate paths and object/artifact identity. Importing/publishing data must not
execute repository hooks or arbitrary code; running project tests is a separate,
policy-governed execution step.

Acceptance:

- Builder → verifier → lead works without sharing a filesystem or GitHub write key.
- New/deleted/renamed/binary files, stale bases, conflicting edits and invalid
  artifacts have explicit outcomes.
- Handoffs survive process loss and duplicate delivery without blindly applying
  changes twice or reporting unverified work as integrated.

### C5 — Harness-managed GitHub publishing

Add optional trusted GitHub integration for private-repository staging and branch
publication. Read access and write access are separate. Keep credentials in the
trusted harness/publishing service; the child submits an artifact and constrained
publication request. Reuse C4's revision/evidence contract.

Enforce stored connection references, permitted repositories, branch prefixes and
operations independently of model input. Pin the expected remote state. Publication
does not implicitly authorize force-push, PR creation, merge or administration.
Return durable operation status and the published commit/branch identity. Lost
responses remain unknown until the original remote state is inspected; no automatic
mutation retry, alternate repository or fallback credential mode.

Acceptance:

- Qualify locally against a controlled Git remote and deterministic API fixtures:
  allowed publication, denied scope, existing-branch conflict, response loss and
  credential expiry/revocation.
- Credentials never enter child tool results, Git URLs or observation payloads.
- A live GitHub write test requires a separately authorized disposable repository;
  it is not implied by approval to run local Docker/Kubernetes qualification.

### C6 — Explicit sandbox Git CLI access

Add the opt-in CLI mode using provider-supported credential-helper/secret delivery.
Keep private checkout and publishing permissions separate, avoid persistent tokens
in Git configuration/URLs, and honor expiry, refresh and revocation. State clearly
that a credential delivered into a shared sandbox is potentially accessible to
other processes in that sandbox. Never switch to this mode after a publisher error.

Acceptance:

- Authorized CLI fetch/push works against a controlled repository; denied or expired
  access fails clearly, and unsupported delivery cannot broaden host access.
- Logs, events, configuration and Git remotes do not expose tokens. Credential
  cleanup and shared-versus-isolated scope are verified.

### C7 — Combined resilience and handoff qualification

Run all three presets, then two concurrent teams with multiple builders/verifiers
and shared/isolated/mixed placements. Combine deterministic failure barriers with
bounded live-model work using an authorized credential source. Exercise restarts,
worker replacement, parked approvals, cancellation, budgets, quotas, workspace
retention, handoff conflicts, unknown publication outcomes and cleanup.

Keep repeatable fast tests, real database/process tests and local Kubernetes/
OpenShell scenarios. Expose useful lifecycle/handoff identifiers through events
and existing observability. Produce grouped test-case documentation and concise
developer examples. Record actual platform/provider/storage qualification instead
of inferring it from compilation or mock results.

Acceptance: each supported variant works end to end, existing SDK/service suites
pass, no unowned resources are changed, and every remaining limitation is documented.
Cloud deployment trials follow this sequence under their own budget/authorization.

## Progress ledger

| Milestone | Implementation/evidence | Result |
| --- | --- | --- |
| Planning | Linked design, current team ledger and this sequence; retained existing completed foundation. | Ready for review. No runtime changes. |

For each verified slice, append the source commit, meaningful tests, local fixture
cleanup, remaining gaps and merge commit. Earlier foundation evidence remains in
the linked team ledger; this plan does not count those checks as newly rerun.
