# Child sandbox placement implementation

This is the C3 implementation note, linked from the
[coding milestone journal](CODING-AGENT-PRESETS-PLAN.md). The agreed behavior is in
[team types and sandbox placement](TEAM-TYPES-AND-SANDBOXES.md). C3 is in progress;
request/selection tests do not qualify provider provisioning.

## Trusted flow

```text
Lead requests a named instance and sandbox choice
  → core checks app ceiling + original approved type envelope
  → reserve instance and immutable choice
  → trusted controller records original provider intent and quota ownership
  → OpenShell create once; uncertain replies permit original-object inspection
  → persist binding and prepare this environment's workspace
  → atomically admit child + initial task
  → existing worker/model loop attaches ToolRunner to that original binding
```

The harness/controller remains outside the sandbox. It does not expose gateway
credentials to the lead, child or tool process. The future sandbox-contained-loop
OpenShell RFC remains a separate story.

## Initial provider support

Use activated, immutable RuntimeProfile IDs, not movable aliases, for delegated
profile references. Recheck the submitting principal, activated source release,
target profile and child release before allocation/admission. Both the app's
delegation and the actual lead profile bound permitted resources.

The first OpenShell adapter uses the existing image and deny-network policy,
independent `/sandbox` filesystems, and explicitly registered profile CPU/memory
limits. Requested limits must match a configured profile; unsupported smaller
resource combinations must reject rather than run with larger limits. Applications
can register several bounded profiles. Arbitrary file mounts, network grants and
credential delivery remain unsupported in this adapter until actually enforced
and qualified; nonempty requests reject, even if a config lists their names.

Team selection retains the original binding. Member selection may reference only
an existing member of this team, with a compatible, Ready, shareable binding.
Reserved targets may wait for their original provisioning; retired/failed targets
must not cause allocation or rebinding. A self-reference or unknown future member
rejects. A custom type's instance selection cannot enlarge or switch its exact
approved sandbox envelope; prebuilt types remain bounded by app delegation and
their activated release's permitted profiles.

New selections allocate one environment per instance, never one per type. The
operation and environment IDs, principal/project, target profile identity,
requested/enforced resources and dispatch status must commit before provider I/O.
Pending/unknown resources count against existing deployment/principal quotas.
Controllers may inspect after a lost create response; they may not repeat create
or choose a replacement identity. No source files or Git metadata are implicitly
shared with a new environment; source staging is C4.

## Runtime and lifecycle

Release execution identity is independent of resource placement, but each runtime
provider/worker selector must match the actual profile. Prebuilt releases already
have profile-specific worker factories. Private type factories must select their
approved target profile while preserving source instructions, model/reviewer and
approval configuration. A temporary template is not proof of provisioned authority.
Admission needs the original environment binding, not just a profile label.

Workspace preparation follows binding resolution. Shared worktree defaults apply
to shared team placement; new independent environments start at their own root
unless an explicitly supported workspace was staged there. Never run a requested
New/Member choice in the lead environment because preparation is unavailable.

Default retention preserves owned work after retirement. Closing a child releases
its agent admission, not the environment or files. Environment records remain
visible and count against quota until explicit, confirmed deletion. Active session
and attachment references must prevent deletion, including references from another
member reusing that sandbox. Cleanup must use the original provider identity and
the existing independent deletion/reconciliation path. Provider-local files are
persistent only for that sandbox's lifetime; an independent disk/volume is not
implied.

## Qualification gates

Contract tests: original type/default after new revisions; excess grants/resources;
immutable choice/retry; unknown/self/retired member; no unbound admission; no
unsupported-adapter fallback. Database/controller tests: authorization revocation,
quota serialization, crash after dispatch, original binding, reference counts,
retirement and deletion. Real OpenShell tests: shared/reused/new environments,
distinct filesystems, actual resource enforcement and original-resource cleanup.

Enable the service placement capability only with validated application delegation
and verified provider/admission behavior; the journal records qualification status.
SDK request fields alone do not enable provisioning. Existing local sandbox-off adapters
continue rejecting New/Member requests until they have a real qualified provider.


## Implementation and operational events

`service_team_environments` is the provider outbox. It pins the original environment,
source release, target profile/spec, actor/project, dispatch marker and current
claim. Reclaimed jobs have inspection authority only. Claim tokens, the environment
revision and binding update commit in one transaction. Lead/team/outbox lock order
matches admission. Allocation and ordinary service admission share the quota lock;
unknown resources keep ownership and count against quota.

The controller resolves New and Member bindings before workspace preparation.
`placement_bindings` is private team metadata, hidden from public snapshots.
Session validation refuses a different provider object even when its profile is
the same. Workspace scope digests and receipts follow the selected sandbox.
Private runtime worker factories retain the approved bundle/model identity while
selecting that sandbox's exact configured CPU/memory profile.

`team_environment` events report pending, unknown, bound and denied admission.
The first unknown create observation also writes a durable lead inbox notice.
Readiness does not introduce an extra wake. Retirement preserves resources;
delete guards include pending/active member references and team-created allocations.
The existing original-identity deletion queue also handles team-owned environments.

The expanded qualification caught a real audit/session deadlock. Security-audit
appends now happen after session/team/environment locks, while remaining in the
same transaction as receipts and events. A deterministic barrier test protects
that ordering; infrastructure errors retain a sanitized type so they cannot be
misclassified as a permanent authorization denial.
