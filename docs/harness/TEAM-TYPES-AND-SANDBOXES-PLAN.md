# Team types and sandbox placement: plan and progress ledger

Branch: `feat/team-types-sandboxes-20261031`; base: `f13de9b9`.
Target after qualification: `openworker-harness-sdk`.
Accepted design: [types, membership and two sandbox stories](TEAM-TYPES-AND-SANDBOXES.md).
Previous completed work: [named teams ledger](AGENT-TEAMS-IMPLEMENTATION.md).

## Milestones and implementation order

1. **Delegation/type contracts.** Typed, bounded definitions and named selections;
   lead-session scope, immutable revision/digest, collision checks, per-request
   subset validation, shared-environment policy compatibility and explicit limits.
   Test invalid/duplicate input, capability escalation and scope/digest confusion.
   This foundation alone does not expose a new runnable tool.
2. **Durable approved types.** SQLite/PostgreSQL definition and approval receipts,
   pending/approved/denied transitions, safe redefinition/restart and per-lead
   catalog. Wire `team_define_type` through the real approval path in Ask,
   Auto-approve and Bypass. Compile constrained definitions without mutating the
   global application registry. Add SDK/service API parity and approval-card data.
3. **Sandbox-aware spawning.** Extend idempotent spawn with a pinned type revision
   and team/member/new placement. Persist provisioning intent, reuse the trusted
   gateway adapter, enforce selected grants/resources and attach original provider
   IDs. Provider capabilities must explicitly cover each requested restriction and
   storage mode; unsupported requests reject, never fall back or broaden access.
4. **Member lifecycle.** `team_manage` list/retire, graceful/cancel modes, admission
   and inbox/wake gates, cancellation reconciliation, capacity and owned-environment
   release. Preserve history, stable identities and unknown-result receipts.
5. **Parallel and mixed-placement qualification.** Real SQLite/PostgreSQL, SDK
   process/storage matrix, two concurrent teams with multiple builders/verifiers,
   shared/independent/mixed environments, crash/approval/cancel races and telemetry.
   Repeat on owned local Kubernetes/OpenShell with bounded live-model work, then
   merge only verified functionality. Record storage capabilities and limits.

The future in-sandbox OpenWorker loop and RFC 0021 MCP integration have their own
follow-up milestone after a qualified upstream version exists. They are not part
of the current implementation or an automatic alternative provisioning path.

## Validation gates

- The lead cannot bypass its app-authorized delegation ceiling, including in Bypass.
- Approval binds a concrete type revision; another lead or changed definition cannot
  reuse it. Pending approval never creates a child or sandbox.
- Child instructions cannot replace trusted approvers or permission policy.
- Equal named grants are resolved/identity-checked by the trusted adapter; names
  alone are not proof of access or provider enforcement.
- Different hard file/network/credential scopes require separate enforcement
  contexts; a shared directory or different tool list is not a sandbox boundary.
- A lost create/cancel/delete response is reconciled against original identities.
  No automatic replay, replacement sandbox, provider switch or external-effect retry.
- New/retiring work observes finite capacity and resource limits; cleanup cannot
  delete an environment another member owns or still uses.
- Existing shared-team and legacy work-item behavior remain covered. No new tool
  is advertised as implemented before its real dispatch path is qualified.

## Progress

### 31 October 2026 — decisions and baseline recorded

- Recorded the accepted two stories and RFC head/restrictions. Current work uses
  the trusted outside harness and existing gateway API; the future lead/child
  loops inside OpenShell use its sandbox-local MCP endpoint.
- Documented current Rust team domain, both storage adapters, SDK/service paths,
  shared environment, scoped-registration gap, approval and retirement gaps.
- Confirmed Python's worker discovery/staffing/connector-grant precedents. Its
  fixed initial roster and approval behavior are not copied as new Rust policy.
- Created an isolated feature branch. Azure remains in its own worktree/branch;
  no cloud provisioning is authorized by this team implementation.
- Starting milestone 1. Later runtime/approval/provisioning capabilities are not
  exposed by this documentation or the initial policy contract.

### M1 — policy/type foundation complete locally

- Added runtime `team_definition.rs`: bounded definitions, named file/network/
  credential selections, permitted placements/resources/models and host-owned
  delegation ceilings. No new dependencies or runtime tool registration.
- Added immutable binding material for lead-session identity, exact instructions,
  tools/settings, revision and resolved policy digest. Revalidation checks both
  the current ceiling and original binding. This is not an approval receipt or
  proof that a provider has enforced the settings; trusted adapters must do both.
- Shared-sandbox compatibility requires trusted resolved-policy fingerprints;
  matching profile names or different tool lists do not prove isolation.
- **12 new contract tests pass**. The targeted regression batch passes **122 tests**:
  72 runtime unit, 18 core, eight SQLite team storage, 12 existing coordination
  tests and the 12 new definition tests. Strict all-target Clippy for runtime,
  PostgreSQL store, SDK and reference server passes with test-support features.
  Formatting, diff checks and Markdown link/code-fence checks pass.
- A parsing regression caught extra fields being ignored for a unit-style shared
  placement variant. A strict struct-style variant now rejects them; the negative
  cases cover policy/image/key smuggling and approval/launcher overrides.
- No model-facing definition tool, approval card, new environment or retirement
  behavior is exposed yet. M2 remains the real storage/approval/runtime wiring.
  Serialization roundtrips here are not database restart qualification.

## M1 test groups and purpose

Source: [definition contract tests](../../harness/runtime/tests/team_definition.rs).

| Group | What it checks | What this establishes |
| --- | --- | --- |
| Delegation limits | Excess tools, files, network, credential references, CPU/memory, profiles and models reject; omitted new-sandbox grants are empty. | A requested definition cannot silently enlarge the supplied ceiling. |
| Input boundaries | Unknown approval modes, reviewer settings, launchers, raw policies/images/keys and invalid placement data reject; text/count bounds hold. | Model-authored JSON cannot smuggle host-owned configuration through this contract. |
| Identity and reuse | Pre-built/reserved names reject; scope, policy, revision and prompt/tool/model changes alter or invalidate bindings. | Approval wiring can bind an exact reusable definition without cross-lead or changed-definition confusion. |
| Sharing semantics | Only equal valid resolved fingerprints are compatible; labels and empty strings reject. | The adapter must establish actual enforcement identity before attaching a child to shared access. |

Run locally without a model, database server or Kubernetes:

```sh
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --test team_definition
```

Next: durable pending/approved/denied definitions and canonical approval handling,
then constrained compilation and scoped catalogs across storage/process modes.
