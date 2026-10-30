# Harness release candidate

This is an additive source snapshot on public OpenWorker main. It is a branch for
integration testing, not a release announcement or a claim of stable APIs.

## Scope

- Rust runtime, Rust/TypeScript SDKs, native Tools SDK, SQLite/PostgreSQL storage,
  server reference, artifacts, telemetry and deployment references.
- Arcade Studio and the minimal desktop starters remain available.
- Lantern/Luma application code, custom app runtimes and obsolete app-specific
  partner-kit packaging are omitted. Notification/device/computer tool crates,
  core approval/MCP tests and independent stdio framing tests remain.
- Private partner notes, credentials, raw deployment logs and generated state
  remain excluded. Public implementation ledgers and sanitized qualification
  evidence accompany the service integration. AWS scripts require an explicit
  target account/role and budget notification recipient.
- Existing Python Desktop source is unchanged. Runtime fixtures now live inside
  `harness/tests/contracts/`; the Rust workspace does not need an external fixture
  directory just to compile.

## Layout migration

The Rust workspace is `harness/`; public guides are `docs/harness/`. Update local
path dependencies, executable paths, build caches and scripts that referenced an
older directory layout. Rust crate names and `@openworker/harness` remain the same.
Generated partner workspaces contain path dependencies: regenerate them against
the new workspace instead of editing installed binaries.

Synthetic examples use `examples/assistant` and `example_echo`; reusable native
tools use `notifications.*` and `device.*`. These example names are not aliases for
private application tools. Existing installed applications remain on their pinned
revision until their owner deliberately updates and requalifies them.

## Verification for this snapshot

Checks repeated during the 2026-10-26 source-layout migration:

- Rust formatting and strict workspace Clippy; SDK/custom-host compilation.
- Linux runtime unit/integration suites, including native tools, Anthropic fixtures,
  compaction, approvals, SQLite teams, model connections, MCP and resource grants.
- Mac SDK desktop/provider-auth tests and the retained native tool crates.
- Deterministic server, filesystem-artifact and telemetry tests. The opt-in S3
  integration test was not run because no object-store fixture was configured.
- TypeScript configuration/transport tests and real-host provider-auth/OpenRouter
  streaming fixtures. These fixtures use local HTTP servers, not live provider keys.
- Independent MCP acceptance: qualified tool selection, SSE discovery, approvals,
  restart, Q&A, denial, redirects, cancellation and external-server ownership.
- Arcade: 27 tests and the production frontend build with Node.js 24. A freshly
  generated Electron starter also installed dependencies and built its frontend.
- Starter/custom-host generator tests, retained raw-stdio tests, deployment-script
  tests and Task Desk/console tests. One startup-cancellation test was skipped
  because its opt-in stack fixture was not configured.
- Release-layout checks: workspace members, removal of legacy apps, relative
  documentation links and source-checkout starter commands. Gitleaks found no
  secrets in the staged snapshot. The branch starts from public main without
  importing private commit history.

- Linux SDK/PostgreSQL matrix: **123 passed**, zero failures, against an isolated
  PostgreSQL 16.4 Docker fixture. Includes SQLite/PostgreSQL embedded/subprocess
  team parity, dynamic agents, auth rotation, ownership fencing, durable controls,
  queue ordering, authorization, worker death, database loss and draining. The
  opt-in load benchmark and mixed-version upgrade drill were explicitly excluded;
  the latter requires a separately qualified baseline worker binary.
- The SQLite embedded/subprocess agent regression also passed in a separate,
  serialized run with the full workspace test-feature configuration.

The HTML QuickStart was checked at the source/link level. A fresh browser render
was blocked by the local file-URL policy. No fresh GUI, signed installer, Windows
VM, live-model, AWS or OpenShell cluster qualification is claimed by these checks.
Generator dependency installation used npm 10 with an engine warning; use the
documented npm 11+ for supported starter development.

## Service integration baseline — October 30, 2026

`openworker-harness-sdk` now includes `feat/openworker-service-m1` through
`eee4391f`. The integration is a fast-forward: the runtime/SDK/service source is
identical to the qualified feature-branch source, with this checklist and its
ledger updated afterward. This remains an integration branch, not a public-main
merge or release announcement.

- Authenticated service clients, RuntimeProfiles, OpenShell provisioning, management
  UI, `ows` deployment commands, durable artifacts and observability are included.
- Observability M1–M5 qualification covers PostgreSQL recording, model accounting,
  authorized run search, management charts/timelines, collector export and Langfuse
  reconciliation. See the [observability ledger](OBSERVABILITY-PROGRESS.md).
- A real Claude Sonnet 5 agent ran file/shell tools inside local Kubernetes /
  OpenShell. All 22 run-scoped records and 10,560 input / 222 output tokens matched
  Langfuse; the management UI and Langfuse UI were visually verified. Records
  survived replacement of all four Langfuse data-service pods with retained PVCs.
- The catalog-worker instrumentation fix passed the real PostgreSQL/OpenShell
  service suite, 13 server-reference tests and eight Helm tests. Earlier outage
  trials retain their gaps; final qualification used a fresh explicit turn.
- The **next cloud gate is a fresh AWS installation of this consolidated
  baseline**, including real agent execution, recovery, telemetry and expiry
  cleanup. Earlier AWS qualifications do not certify this newer baseline. GCP
  and Azure require their own deployment qualification.

Evidence: [live dual-UI result](evidence/observability-live-20261030/qualification.json),
[persistent local setup](../../deploy/reference/LANGFUSE-LOCAL.md),
[service progress](SERVICE-PROGRESS.md).

## Canonical integration branch consolidation — October 30, 2026

Fast-forwarded `openworker-harness-sdk` to the verified GCP feature work through
`e4f96969`, preserving all four commits after the earlier `6e1bc173` baseline.
There was no branch divergence or source conflict. This is the canonical public
feature-development and partner-integration branch; tested feature milestones
should return here regularly instead of accumulating only on working branches.

- GCP identity bootstrap/direct preflight, private Terraform graph export, local
  lifecycle journals and the release-size baseline are now included.
- The exact merged CLI source passed **75 tests** and real pinned Terraform/provider
  schema validation before integration. The fast-forward does not change that
  source; subsequent consolidation changes are documentation only.
- At this consolidation checkpoint, GCP remote provisioning and independent
  cleanup were unimplemented gates. The local lifecycle milestone below supersedes
  that implementation status without claiming a qualified GCP deployment.
- The Python desktop harness is unchanged. This consolidation does not merge into
  public `main`, publish packages or announce a stable SDK release.

See the [GCP ledger](OWS-GCP-TRIAL-PLAN.md) and
[binary footprint baseline](BINARY-FOOTPRINT.md). Partner builds should pin a
revision and keep SDK/runtime versions aligned.

## GCP lifecycle recovery and independent cleanup — October 30, 2026

The verified milestone is ready to return to `openworker-harness-sdk` from
`feat/ows-gcp-lifecycle`. Changes are confined to the management CLI, cleanup
container and documentation. The runtime, Rust SDK and TypeScript SDK sources are
unchanged from the previous integration checkpoint.

- **95 CLI tests pass**, including loopback transport, lost responses, checkpoint
  compare-and-swap, ownership fences and scheduler pause reconciliation.
- Pinned Terraform validates both control foundation and runner graphs with zero
  errors/warnings. A Linux AMD64 cleanup image builds and passes network-disabled,
  read-only, unprivileged container smoke checks.
- Production workload creation remains closed in code. No new cloud mutation,
  IAM grant or paid resource was created during this milestone. Full preflight,
  source/control installation and live independent cleanup remain to be qualified.

See the [lifecycle guide](OWS-GCP-LIFECYCLE.md), [progress ledger](OWS-GCP-TRIAL-PLAN.md)
and [sanitized evidence](evidence/gcp-lifecycle-cleanup-local-20261030.json).

## Gates before announcement

- Several days of Arcade/partner dogfooding, including real provider streaming,
  auth expiry, pending approvals, cancellation and restart recovery.
- Clean-checkout builds and packaged app acceptance on target Windows/Mac OS and
  CPU architectures; code signing/notarization and runtime prerequisites.
- Explicit Linux/PostgreSQL/OpenShell deployment qualification for the selected
  versions. Local fixture tests do not certify AWS/Azure or every cluster policy.
- Real local-model/tool compatibility, image consent and failure UX.
- Dependency/asset license review and release version/package publication decisions.
- Review public API/source compatibility before tagging a release. Package versions
  remain development identifiers, not a promise of semver stability.

No live provider credentials, AWS provisioning, external messages or model-controlled
desktop actions are needed for the deterministic regression suites.
