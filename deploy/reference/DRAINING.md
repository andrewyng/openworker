# Planned worker shutdown

The harness owns draining; Kubernetes or another supervisor owns replacement.
This concerns server workers, not the Desktop agent loop or evacuating a host
that also runs OpenShell sandboxes and their volumes.

## Contract

First SIGTERM/Ctrl-C requests a drain. Stop new session claims, queued follow-up
turns and approval/question resumes. Admissions already underway may finish
before **acknowledgement**: an exclusive barrier waits for their shared admission
guards. The CLI prints acknowledgement after that boundary; this does not mean
all tools have finished.

Already-running turns continue their model/tool steps under existing policy until
they finish or park. Leases keep renewing and active cancellation still takes
priority. Pending interactions and accepted responses stay durable for another
compatible worker. Drain does not wait for a human or an endlessly refilled queue.

After execution settles, normal cleanup releases ownership. On drain timeout,
use existing interruption/unknown-effect recovery and exit unsuccessfully with
an explicit diagnostic. A second signal requests immediate interruption. DB,
lease and runtime failures also interrupt without waiting for the drain deadline.
Never retry an uncertain tool just because the old worker exited.

```sh
openworker-server-reference --config /private/reference.json worker \
  --node worker-a --slots 2 \
  --drain-timeout-seconds 300 --shutdown-timeout-seconds 45
```

Drain bounds: 1–3600s; cleanup: 1–120s. Defaults are 300s and 45s. The drain timer
includes waiting for admission acknowledgement. An uncooperative native tool
cannot be forcibly stopped safely in-process; the supervisor remains the final
termination boundary. Durable recovery is still required for forced kills.

Trusted Rust server hosts use `openworker_store_postgres::worker::Drain` and the
additive `Worker::run_with_drain(drain, abort_token, timeout)`. The one-shot handle
has `request`, `is_requested`, `acknowledged` and `is_acknowledged`. Race the
acknowledgement against the worker result: a failure may exit without acknowledging.
Existing `Worker::run(shutdown_token)` retains immediate-stop semantics. No remote
drain route or high-level Harness SDK protocol change is introduced.

Telemetry: advertised new-work capacity becomes zero on request; occupied slots
stay visible and `openworker.worker.draining=1` marks acknowledgement. Heartbeats
continue until exit (5s reporting interval). A planned loss of capacity can still
legitimately alert if no replacement exists.

## Supervisors

Helm passes explicit timeouts and requires a termination grace of at least
`drain + cleanup + 15s`, default 360s. RollingUpdate uses `maxUnavailable: 0`, `maxSurge: 1`. The
local reference launcher uses the same 300/45/15 budget, replacing its previous
35s worker cutoff. APIs retain separate HTTP/SSE shutdown; clients reconnect and
replay durable events. Configure other supervisors with a compatible budget.

Pod readiness is not proof of compatible execution capacity. The chart has API
readiness but no comprehensive worker/model/OpenShell readiness probe. Verify a
compatible replacement pool with a canary and capacity before retiring an old
pool. RollingUpdate limits do not promise uninterrupted agent execution. This
feature does not drain Kubernetes nodes or move OpenShell workspaces/PVCs.

## Version-pinned sessions

Execution identity pins coworker bundle, policy and runner. Native registry
sessions also pin the host executable. A new native binary can intentionally be
incompatible with an existing session even when the DB schema/protocol matches.

Keep an old compatible worker pool for old sessions, and send new sessions to
the new identity. Do not remove all old workers while old queued work/approvals
remain. Drain a replica only when another compatible replica can handle remaining
work. Restoring an old pool can resume its old session, but does not make it
compatible with new sessions. Never rewrite identity/binding digests to force it.

Transparent migration/re-approval needs a separate design. This slice does not
weaken pins, migrate storage schemas, promise arbitrary binary rollback, or
certify a complete Kubernetes upgrade.

## Verification

For side-by-side releases, retirement diagnostics and creation rollback, see
[Version-aware deployment](UPGRADES.md). Draining alone does not migrate sessions.

Five real Linux/PG process cases cover current execution completing across lease
renewals; queued follow-up preservation; approval-response deferral versus active
cancellation; deadline/unknown effects without replay; DB loss during drain; and
mixed native identities with a retained baseline pool. Two barrier units cover
in-flight admission and timeout before acknowledgement. Separate HTTP/OpenShell
qualification exercises actual server SIGTERM, parked approvals, API/worker
replacement, remote tools, SSE and artifacts.

```sh
# harness/; OW_PG_TEST_URL must identify an isolated local test database.
cargo test --locked -p openworker-store-postgres --features test-support \
  --test worker_recovery draining:: -- --include-ignored --test-threads=1
```

For the mixed-version case, set `OW_BASELINE_WORKER` to the retained `2a5c208`
test executable; without it, that case returns early and is not version evidence.
Set `OW_CONCURRENCY_REPORT_DIR` to an absolute private evidence directory. Native
probes are scripted and explicitly unsandboxed, distinct from the real OpenShell
transport test. Only owned test resources are cleaned up; Task Desk is unchanged.
