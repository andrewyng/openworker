# Local deployment reference

Run two native harness APIs and two workers against local PostgreSQL,
S3-compatible SeaweedFS, and optional Grafana/LGTM. The separate read-only
OpenWorker Console uses the HTTP APIs, never PostgreSQL. No AWS account, API key
or live model is required for qualification.

This is a **single-machine development/operations reference**, not an HA cluster.
Backing services run in Docker Compose; the harness and console are native host
processes. Actual tool execution uses the existing, separately provisioned
Kubernetes/OpenShell gateway. No Docker socket mounts, privileged containers,
unsandboxed tool fallback, public ingress or changes to Desktop/stdio/SDK.

## Before starting

- Docker with Compose v2+; Node 20+; Rust 1.96. Linux/macOS controller profile.
- A qualified OpenShell 0.1.3 Kubernetes gateway with persistent workspace,
  verified TLS/mTLS and an immutable Linux runner image. Follow
  [the local Kubernetes setup](../local/README.md). Keep its kubeconfig explicit;
  these scripts never change the default context or provision a gateway for you.
- An absolute path to the corresponding Linux ELF runner (even on a Mac). Keep
  the gateway/image/runner identity consistent with your verified harness build.
- About 3–4 GiB available container memory with optional observability, plus
  space for the pinned images/data. Grafana/LGTM is a development bundle, not our
  recommended production topology. Each dependency retains its own license.

All host bindings are literal `127.0.0.1`. Defaults: APIs 58181/58182, console
54340, PostgreSQL 55461, S3 58333, Grafana 53000, OTLP/HTTP 54318. Separate Compose
networks isolate backing services from each other. Only S3's authenticated port
is published, not SeaweedFS's admin/filer interfaces. No remote telemetry export
or eBPF instrumentation is enabled.

## Start

Run from the repository root. Copy/edit `connection.example.json` outside the
repository with your gateway endpoint, certificates and pinned runner/image.
It contains paths, not private-key contents. Do not use the placeholder image.

```sh
cargo build --locked --manifest-path harness/Cargo.toml -p openworker-server-reference
node deploy/reference/stack.mjs init .ow-stack /private/connection.json
node deploy/reference/stack.mjs up .ow-stack
node deploy/reference/stack.mjs run .ow-stack
```

`init` refuses an existing state directory; it never replaces credentials or
an existing deployment. To omit telemetry/Grafana, append `--no-observability`
to `init`. To run another independent deployment, use a new private state path
and `--port-offset 1000` (0–7000). Retain the generated deployment manifest and
Compose environment file; volume/project identity is tied to them.

`up` creates backing services and retains data in project-scoped named volumes.
`run` explicitly installs the current PG schema/optional extension, launches
APIs/workers and the console, and waits for API readiness. Set `OW_SERVER_BINARY`
to an absolute alternate native build if needed. It owns its child handles,
not arbitrary PID files. Ctrl-C/SIGTERM stops only its native children; normal
shutdown has a bounded wait and then terminates a remaining child. It does not
silently retry failed agent actions or auto-restart poisoned workers.

Worker shutdown now permits a 300s drain and 45s cleanup with a 15s margin;
the native launcher waits that budget before forcing termination. APIs/console
keep their separate stop behavior. See [planned draining](DRAINING.md), including
why a new native worker binary is not automatically compatible with old sessions.

Open the printed console URL. Its separate access key is in
`.ow-stack/private/console-key`. Grafana uses username `admin` and the generated
password in `.ow-stack/private/grafana-password`, **not** `admin/admin`. Use a
password manager or copy locally; never put either key in a URL, issue or commit.
The API key stays server-side and is distinct from browser access credentials.
The console uses `private/observer-token`; `private/api-token` is administrative
and retained for the trusted local test CLI. `run` idempotently provisions the
registry without reviving revoked credentials. For a state directory created
before authorization was added, stop native processes, start backing services,
then explicitly run `node deploy/reference/stack.mjs authorize .ow-stack` once.
This preserves existing sessions/keys and switches the console to an observer.
See [roles, scopes and rotation](../../harness/server-reference/AUTHORIZATION.md).
To rotate a revoked console key, stop `run`, issue a fresh observer using private
files and a new request ID, and update `deployment.json`'s `observer_request_id`
to that ID alongside `private/observer-token`/`observer-spec.json`. Keep the
administrative key separate. Restart does not invent a replacement or un-revoke
the old key; matching issuance only verifies the explicitly configured record.

## First real local exercise

Stop `run` first. This deliberately interrupts only this deployment's owned
native processes and backing services; never aim it at a customer deployment.

```sh
node deploy/reference/qualify.mjs .ow-stack
node deploy/reference/stack.mjs run .ow-stack
```

The test retains example sessions/artifacts and one explicitly shared
qualification sandbox for inspection. Its model is scripted, but tools,
PostgreSQL, S3 bytes, processes and gateway are real. It checks:

1. Pending approval identity after crashing both workers and replacing an API.
2. Approval through the surviving node, real file write/read and contiguous SSE.
3. Partial streamed upload staying pending; exact retry/download across APIs.
4. Authenticated console read failover and refusal of mutations.
5. PostgreSQL outage, explicit process reconnect, S3 restart and retained bytes.
6. Agent completion with the collector offline, visible export failures, then
   real metrics/traces/logs queried through authenticated Grafana.
7. A complete Compose down/up, with original approval identity and artifact
   checksum preserved in the named volumes.

The result is written to private `qualification.json`; logs go to private
`logs/`. Rerunning adds new test sessions but reuses the original environment
receipt; an unknown environment is never automatically recreated. The shared
sandbox is for these mutually trusted fixtures only, not different customers.
Do not approve or change a qualification session while its test is running.

To submit your own task, use the existing
[reference client](../../harness/server-reference/README.md) with the printed
API ports, API-token file and environment ID from `qualification.json`. The
default scripted model is for protocol tests. Live use requires deliberately
changing `scripted`/`model` and supplying a provider credential to the worker;
the local setup never reads or imports an existing OpenWorker credential.

## Optional UIs

**Console:** sessions, pending approval/question details, recent conversation,
execution events, artifact metadata and authenticated downloads. Its session
directory is bounded/keyset-paginated. “In this view” and “Need attention” count
loaded sessions, not the whole database. State comes from durable APIs; a
telemetry outage cannot erase a pending decision. The UI has no approve/cancel/
delete controls. Use your app/CLI for those.

**Grafana:** provisioned `OpenWorker · Harness operations` dashboard, plus Explore
for Tempo traces and Loki logs. Panel queries use measured harness operations,
latencies, errors, reported tokens/retries, policy/decision transitions, recovery,
unknown effects and telemetry health. There is no invented active-session gauge,
approval-wait timer, total-cost calculation or successful-tool retry. A missing
sample remains no data, not zero; scripted model runs do not report token usage.
Durable logical-turn context preserves linked execution segments across worker
changes; other lifecycle activity also correlates through pseudonymous hashes.
See [telemetry configuration and limits](../../harness/telemetry/README.md), and
[collector/Langfuse qualification](EXPORT-QUALIFICATION.md).

Both UIs are optional. Customers can keep their own UI and OTel backend. The
console is a small buildless Node/browser app without framework/npm runtime
dependencies; its [integration and security guide](../../harness/console/README.md)
explains standalone use. Grafana's admin can change dashboards/settings; the
console's observer cannot modify harness state.

## Stop, restart, recover

```sh
# Stop native processes with Ctrl-C in `run`, then:
node deploy/reference/stack.mjs down .ow-stack
# Later, reuse the SAME state and volumes:
node deploy/reference/stack.mjs up .ow-stack
node deploy/reference/stack.mjs run .ow-stack
```

`down` does not pass `--volumes` and does not delete secrets, data or the separate
OpenShell sandbox. No blanket prune/reset command is provided. Named volumes
are persistence, **not backup**. Keep credentials and backup data together;
deleting/recreating secrets while retaining databases breaks authentication.

| Failure | Operator response |
|---|---|
| API disappears | Console retries GET on the other API before forwarding bytes. SSE reconnects/reconciles; no mutation retry. Restart `run` when appropriate. |
| Worker crashes | New worker incarnation claims after lease expiry and inspects durable state. Pending approvals retain identity. Started effects without results are unknown; never blindly repeat them. |
| PostgreSQL restarts | Existing connections may be poisoned; stop/restart `run` after PostgreSQL is ready. Do not rotate submission/control IDs to force a retry. |
| Upload disconnects | Inspect the same artifact ID. Resume identical bytes or reconcile its stored manifest; never rerun the generating tool merely to repair upload delivery. |
| S3 unavailable | Metadata remains pending/ready as recorded, but delivery fails explicitly. Restore the original store/credentials; no filesystem fallback. |
| Collector/Grafana down | Agent work continues; `/readyz` exposes local telemetry drop/export counters. Lost telemetry is not replayable audit. |
| Browser/server restart | Sign in again; select the durable session. UI cookies are process-local and expire after eight hours. |

Backups/restore, HA/replication, rolling schema upgrades, retention/orphan cleanup,
tenant quotas and production alerting are deliberately not solved by restarting
this local reference. Back up PostgreSQL and referenced artifact objects under
a coordinated policy; a successful process-restart test is not a backup-restore
or multi-AZ test. Use a production supervisor for restart/backoff and alerting.

## Security and production boundary

Generated root/private files are 0700/0600. Compose mounts secret files read-only;
they are not encrypted Kubernetes Secrets or a vault. Database/S3/Grafana keys
are local test credentials. S3 environment keys are omitted from native workers,
and the console does not inherit runtime credentials.
Model/tool sandbox secrets remain governed by the harness's existing policy.

Loopback plaintext is confined to this local reference. Host-local processes
with the same privileges and Docker administrators are trusted. Do not bind
these services to `0.0.0.0` or expose them through a public tunnel. Production
needs authenticated TLS ingress, database/object-store TLS, external secret
management, tenant isolation/admission policy and tested storage/IAM rules.
The console's bearer is now an API-enforced observer credential; operator and
administrator credentials are rejected at console startup. API session scope
does not isolate intentionally shared sandbox files or network access.

Raw session content is sensitive operational data. The browser escapes it as
text and uses a restrictive CSP; artifacts download as attachments, never inline
HTML. Telemetry is content-free and best-effort, separate from durable audit.
No claim is made for native Windows deployment, real AWS IAM/STS/KMS, or physical
multi-node isolation from this local test.

## Verification and upstream references

Optional current-state dashboards and local-only alerts now accompany the
operation telemetry. See [the operations runbook](OPERATIONS.md) for enabling
the sampler, upgrading an existing state directory without rotating secrets,
thresholds, first-response actions and the isolated failure drill. Existing
state directories need generated operations JSON before using the new Compose
mounts. Alerts are muted locally; no external notification is configured.

```sh
node --test deploy/reference/stack.test.mjs harness/console/server.test.mjs
# Optional startup-cancellation test: backing services up, native run stopped.
OW_STACK_STARTUP_TEST_STATE=.ow-stack node --test deploy/reference/stack.test.mjs
# With a running example stack and separately installed Playwright:
node harness/console/browser-check.mjs .ow-stack
```

Optional browser QA accepts `OW_PLAYWRIGHT_MODULE` and `OW_CHROMIUM_EXECUTABLE`
for an existing installation; screenshots stay in private state. It checks
login, real session/approval/event display, file download and mobile layout.
No browser package is installed by the runtime launcher.

Pinned choices/configuration verified against upstream sources:
[Grafana's local LGTM reference](https://github.com/grafana/docker-otel-lgtm/tree/v0.36.0),
[SeaweedFS mini](https://github.com/seaweedfs/seaweedfs/wiki/Quick-Start-with-weed-mini),
[S3 credentials](https://github.com/seaweedfs/seaweedfs/wiki/S3-Credentials).
Digests are in `compose.yaml`. Read the implementation ledger in
`docs/harness/LOCAL-DEPLOYMENT-PLAN.md` for actual test evidence and limits.
