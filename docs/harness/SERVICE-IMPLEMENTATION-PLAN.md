# OpenWorker Service implementation plan

Started 27 October 2026. Design: [service specification](OPENWORKER-SERVICE.md).
Branch: `feat/openworker-service-m1`, based on `7f02f274`.

M1 and the OpenShell-first M2 scope are complete; see the progress ledger for tested scope.

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
3. `ows` CLI and Docker-only local deployment/reference lifecycle.
4. Expiring credentials/human login, managed configuration, worker scaling/draining
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

## M3 next

- [ ] `ows` CLI using the same authenticated service API: configuration, profile /
      release deployment and activation, submission, watching, approvals and cancel.
- [ ] Durable combined session/task admission for CLI submit, including retry keys.
- [ ] Reproducible local container packaging and setup, preserving OpenShell
      compatibility and keeping database/gateway credentials off public ingress.
- [ ] CLI/container end-to-end qualification, restart/reconnect and cleanup commands.

M3 was assessed but not started in the M2 commit. No cloud resources are needed.
