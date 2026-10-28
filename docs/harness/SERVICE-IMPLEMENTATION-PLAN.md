# OpenWorker Service implementation plan

Started 27 October 2026. Design: [service specification](OPENWORKER-SERVICE.md).
Branch: `feat/openworker-service-m1`, based on `7f02f274`.

M1–M4 are complete for their locally qualified scope; see the progress ledger
for evidence and remaining limits. SSO is deferred by user decision.

## Milestones

1. Authenticated `/v1` service contract and Rust/TypeScript `Harness.connect()`.
   Reuse PostgreSQL admission, scoped authorization, durable events and priority
   controls. Support an operator-configured agent and already-registered OpenShell
   environment first; release deployment and RuntimeProfile provisioning are M2.
   Capabilities must explicitly report these limits. One namespace per service
   store in M1, persisted service identity; no claim of multi-tenant namespaces.
2. Agent release deployment/activation, RuntimeProfiles, service-managed environment
   provisioning and shared attachments. Qualify live unknown tool outcomes.
   Advanced imports of application-owned sandboxes remain a follow-up.
3. `ows` CLI and containerized local service/reference lifecycle; OpenShell is
   an explicit prerequisite. A Docker-only execution provider remains separate.
4. Expiring service credentials (SSO deferred by user), managed configuration, worker scaling/draining
   and Kubernetes controller with explicit GitOps/autoscaler ownership.
5. Management UI and public-cloud/provider qualification.

## M1 implementation order

- [x] Define versioned capability/identity/error/idempotency contract; retain legacy routes.
- [x] Add authorized session, turn, control, receipt and SSE service endpoints.
- [x] Add TypeScript remote handles and Rust remote client via Harness.connect.
- [x] Verify durable retry/reconnect, bounded event parsing, credential handling,
      namespace/service mismatches and controls independent of event streams.
- [x] Run local PostgreSQL/API integration plus existing local SDK regressions.
- [x] Qualify an existing local OpenShell environment when available; keep evidence
      distinguishing real tools from synthetic adapter fixtures.
- [x] Update docs/ledger with exact commands, outcomes and remaining capabilities.

## Constraints

No new agent loop, tool execution on API nodes, public bootstrap endpoint,
implicit native-code deployment or bypass of existing grants. SDK disconnect must
not cancel service work. Idempotency keys survive retries; request IDs are tracing.
No cloud resources or production/DLAI changes during M1.

Disk: measured 38 GiB free initially (user estimated ~30 GiB). Reuse the existing
12 GiB native Cargo target and existing Docker images. Use temporary dedicated
PostgreSQL schemas/containers; do not prune other agents' resources. Recheck before
large builds; ask for more space if projected free capacity drops below 10 GiB.

M1 evidence and limitations: [progress ledger](SERVICE-PROGRESS.md).
Runnable interface: [service quickstart](SERVICE-M1-QUICKSTART.md).

## M2 implementation order — 27 October 2026

- [x] Immutable release and RuntimeProfile catalog, explicit administrator
      activation, principal allowlists and idempotent management receipts.
- [x] Atomic provisioning/admission intents: stable operation/environment/session
      IDs before provider I/O. Controller dispatches create once; a lost outcome
      is reconciled against the original object, never automatically recreated.
- [x] Dynamic worker cohorts using the existing worker lease/priority-control
      machinery. One global slot bound, immutable release/profile/runner identity.
- [x] Session admission from default/explicit profile or compatible existing shared
      environment; ownership and project checks; independent conversations.
- [x] SDK management/session helpers and readiness waiting; retained operation
      receipt when waiting is interrupted.
- [x] Typed live remote-tool uncertainty returned to the model without replay.
- [x] PostgreSQL transaction/fault tests and local Kubernetes/OpenShell end-to-end
      tests with coder/verifier releases, resource profiles, sharing and controls.
- [x] Update examples, limits and ledger; assess M3 CLI/container packaging.

M2 initial execution policy uses the qualified OpenShell filesystem/workspace and
network-deny profile. CPU/RAM and sharing are configurable within bounded values.
Native code, arbitrary MCP launchers, credentials and host paths are not uploaded
through release JSON: releases select tools from the installed trusted bundle.
Administrator authorization gates publishing and activation in this milestone;
separate publisher/activator roles remain in M4. Working-directory overrides and
additional network policies must be explicitly rejected until qualified.

## M3 — CLI and local containers

- [x] `ows` CLI using the same authenticated service API: configuration, profile /
      release deployment and activation, submission, watching, approvals and cancel.
- [x] Durable combined session/task admission for CLI submit, including retry keys.
- [x] Reproducible local container packaging and setup, preserving OpenShell
      compatibility and keeping database/gateway credentials off public ingress.
- [x] CLI/container end-to-end qualification, restart/reconnect and cleanup commands.

M3 passed local qualification. OpenShell remains an explicit external prerequisite; the
service roles and PostgreSQL run in Compose. No cloud resources are needed.

## M4 — administration and Kubernetes service control

- [x] Expiring scoped credentials, expiry enforcement during requests/SSE/admission,
      private expiring bootstrap and safe rotation/revocation. Separate publisher,
      release-administrator and service-administrator authority from tool controls.
- [x] Versioned service configuration with expected-revision conflict checks,
      admission pause/quotas and resolved compaction defaults for new sessions.
- [x] Worker-pool desired/observed capacity, explicit manual/GitOps/autoscaled
      ownership, durable reconciliation and graceful service-worker drain.
- [x] Kubernetes service packaging and narrowly scoped controller; reuse existing
      OpenShell gateway, PostgreSQL and execution identity boundaries.
- [x] Local deterministic, database and Kubernetes end-to-end qualification;
      updated CLI/setup documentation and progress ledger.

User confirmed service tokens now and SSO later.
AWS and GCP qualification follow when the user supplies clusters. No cloud
infrastructure is provisioned during M4. OIDC needs an explicitly configured issuer,
audience and login flow; do not invent an identity provider or advertise token-file
login as browser SSO. Record any unqualified authentication modes explicitly.

M4 qualification: local kind/OpenShell, real tool effects and scripted models;
service-token administration, immutable role separation, config revision/pinning,
manual scale 0/1/2, active-tool drain, Helm replica ownership, stale-controller
reporting, API/controller restart, strict release gating and guarded teardown.
Model-provider/secret enrollment over REST and live slot changes remain follow-ups;
those settings are deployment-managed today. M5 starts with the management UI.
AWS/GCP qualification follows when the user supplies those clusters.

## M5 — live management UI implementation order

- [x] Clickable visual mock reviewed by user.
- [x] Add bounded session/catalog/event-page reads with existing scoped authorization.
- [x] Add a separate UI server: per-browser service-token login, HTTP-only expiring
      sessions, same-origin/CSRF checks, fixed upstream and strict route allowlist.
- [x] Connect agents, sessions, controls/results/events, RuntimeProfiles, worker
      pools, configuration and credentials. Preserve revision and idempotency semantics.
- [x] Replace sample charts with explicitly bounded real-data summaries; reserve
      export integrations without pretending exporters are configured.
- [x] Verify access separation, expiry/logout, uncertainty/retry behavior, and real
      PostgreSQL/API browser flows locally. Package a standalone container and document
      TLS/proxy deployment. AWS/GCP remain later qualification.

M5 management UI is implemented and qualified locally against real PostgreSQL and
Rust HTTP, including the packaged Linux UI container. These fixtures verify durable
admission/control requests, not worker execution. Helm rendering/security boundaries
are tested; a fresh combined UI + API + worker/OpenShell Kubernetes rollout and
AWS/GCP qualification remain subsequent qualification, not claimed complete here.

M5 complete-stack local UI + API + worker/OpenShell qualification subsequently
passed in a fresh kind deployment; see [the report](SERVICE-M5-QUALIFICATION.md).
AWS/GCP and other platform/model combinations still require their own evidence.
