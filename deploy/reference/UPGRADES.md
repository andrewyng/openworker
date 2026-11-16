# Version-aware deployment and rollback

The unit of compatibility is a **session's execution identity**, not a pod name,
image tag or marketing release number. Coworker content, runner, policies and
native tool registry/host binary contribute to that identity. Do not rewrite it
in PostgreSQL, including on an approval, to make an upgrade succeed.

## Deploy side by side

1. Back up and run the new server's explicit `migrate` command. This change adds
   an independent versioned operational catalog; session schema v5 is unchanged.
   Earlier workers can still use their original session tables. This is not a
   guarantee for future incompatible schema changes.
2. Keep the old image, coworker, runner and configuration immutable. Start a
   **separate** new release (for example `ow-v2`) against the same database,
   environment deployment and artifact store. New release, new worker pool.
3. Wait for new worker readiness and run a canary using the new API's creation
   template. Verify actual approval, execution and result; readiness is not a
   substitute for that canary.
4. Switch the application's **new-session creation** traffic to the new API.
   Existing session reads, submissions and controls can use either API; the
   exact-match scheduler routes their work using the durable session identity.
   This assumes compatible HTTP/storage protocols and retained credentials.
5. Leave old workers running for old sessions, including sessions waiting for a
   human and idle/completed sessions that may receive another message.

With Helm, use separate release names and immutable images/configs. The chart's
ordinary rolling update is safe only within an execution-compatible cohort.
`maxUnavailable: 0` and readiness **do not** authorize replacing the last worker
for a different execution identity. The chart intentionally does not infer this
from image tags or automatically delete old releases.

## Inspect the remaining work

```sh
openworker-server-reference --config /private/reference.json worker-pools --limit 50
# If `next` is non-null, pass it as --after on the next read.
```

This local administrator command uses the trusted database connection, not an
application bearer token. There is no new public HTTP endpoint or agent tool.
It returns a timestamped page per execution digest:

| Field | Meaning |
| --- | --- |
| `open_sessions` | Includes idle and completed-but-open sessions |
| `active_sessions` | Work not parked/terminal |
| `queued_turns`, `pending_controls` | Durable outstanding submissions/decisions |
| `approvals`, `questions` | Unanswered interactions on the current parked turn |
| `sessions_without_advertised_worker` | No live, non-draining advertisement matches the **full scheduling selector** |
| `workers` | Node, full selector, capacity and draining status; bounded to 100 |

Selectors include coworker, execution digest, backend, scripted/live mode,
fallback model, environment provider, deployment identity and profile. A matching
digest alone is not enough. This is scheduling coverage, not model quota, free
slots, all provider connections, sandbox availability or a per-tenant guarantee.
Inspect `workers_truncated`; pages may change between reads. Counts contain no
prompts, arguments, credentials, workspace paths or session identifiers.

Each server worker advertises using its own store incarnation. Advertisements
expire after 20 seconds using **database time**, refresh every five seconds,
and are withdrawn on orderly exit. Stale rows older than one day are cleaned in
bounded batches. Advertisements are not session ownership leases and cannot
authorize execution. Pre-catalog workers are **unobserved**, not necessarily
absent. Session claims and per-use environment/approval checks remain authoritative.

## Readiness versus liveness

`worker --health-listen 127.0.0.1:8081` exposes a status-only `/readyz` (204/503).
Non-loopback listeners are rejected. Helm probes it inside the container.

Before intake, the worker loads/hashes its coworker/runner and validates its
selector, checks the PostgreSQL catalog, and makes a read-only authenticated
gateway/version check. Ongoing checks refresh readiness; stale checks or an
immediate drain request make it unready. Gateway/database failure interrupts the
worker and exits through normal durable recovery, rather than reopening a
poisoned connection and retrying effects. Liveness only checks process presence.
No extra sandbox is created by readiness, and no model request is made.

The gateway check verifies the qualified OpenShell version/driver and mTLS
connection, **not every sandbox or available cluster capacity**. A session's exact
environment is still inspected when used. The existing 300-second drain plus
45-second cleanup budget applies; termination grace must include both.

## Retirement and rollback

An empty active-work count is not permission to retire a pool. First stop all
creation paths using its old template. Explicitly close sessions through the
existing trusted lifecycle after resolving pending work, or retain the old pool.
The current HTTP adapter does not expose an administrative bulk-close API.
Recheck all pages for zero open sessions, drain the pool, and verify termination.
The report is an **advisory snapshot**, not an atomic creation fence or automatic
retirement controller. Application routing/creation cutoff belongs to the host.

For rollback, switch **new creation** back to the old API/template. Retain both
worker cohorts: sessions already created under the new version still require it.
An API-only image rollback must not accidentally replace the new workers too.
Restore failed compatible workers from their exact pinned artifacts. Do not
claim transparent session migration or silently substitute the old binary for
new-version sessions. Migration/reapproval needs a separate explicit design.

## Local qualification

`deploy/helm/version-drill.mjs` takes an explicit private JSON configuration:
`kubeconfig`, `context`, `helm`, `imageA`, `imageB`, `runnerImage`, `certificates`,
`gateway`. Image refs must be immutable and already loaded into kind. Both images
must include the new server, a compatible runner and an `assistant-example` coworker;
their coworker contents must differ. The current drill targets the documented
local kind context and existing `openshell` test gateway, not arbitrary clusters.

The script creates a random owned namespace and synthetic PostgreSQL database,
fresh credentials, two Helm releases and one explicitly shared test sandbox.
Its model is scripted; MCP/file/shell operations run in real OpenShell. It does
not access Task Desk, AWS or a model key. Raw API forwarding is loopback-only;
this is not a new TLS/load qualification. Network policy is disabled only for
this isolated fixture. Reports and ownership metadata live under private
`.ow-stack/ow-version-*/`. On failure it retains exact resources for inspection;
the operator must remove that sandbox and namespace after recording evidence.
