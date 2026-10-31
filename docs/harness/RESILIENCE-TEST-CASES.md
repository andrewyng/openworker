# Resilience test cases and reviewer guide

This suite checks whether an agent service behaves predictably when connections,
workers and processes stop. It combines deterministic model fixtures with real
storage, worker processes and OpenShell execution. A live LLM is not required:
the fixture controls the exact fault boundary, making failures reproducible.

The progress ledger records the latest qualification and its limitations:
[RESILIENCE-SUITE-LEDGER.md](RESILIENCE-SUITE-LEDGER.md).

## Test levels

| Level | Prerequisites | What it establishes |
| --- | --- | --- |
| Fast | Rust, Node, cached dependencies; loopback sockets | Runtime/storage contracts, model-stream accounting, telemetry/export and UI presentation logic, without Kubernetes. Linux also runs native process-group tests. |
| PostgreSQL | Docker, pinned PostgreSQL image; Linux test image on macOS | Actual durable state, leases, API authorization, independent recording database and worker replacement, without Kubernetes or OpenShell. |
| Cluster | Owned local Kubernetes service, OpenShell, fault-fixture image, Playwright | Actual shared execution, Kubernetes worker deaths, cross-node API controls, process lifetime, environment cleanup and management UI/OTLP correlation. |

Tests that lack prerequisites must fail or be explicitly reported as unavailable;
a skipped fixture is not a resilience pass. These checks are not a throughput
benchmark or a claim of physical-node, disk or availability-zone survival.

## 1. Interrupted model streams and worker recovery

We interrupt output before a complete model response is committed, then reconnect
and read the original durable run. This prevents partial output from turning into
an action and prevents restarts from resetting execution allowances.

| Case | Level | Assertions |
| --- | --- | --- |
| Partial stream failure | Fast | No function call or tool start is committed from an incomplete response; the attempted model request and output reservation remain charged. |
| Deterministic streaming barrier | Fast | A delta is emitted before the injected cancellation; no completed response follows task cancellation. The fixture's wait is bounded. |
| Restart preserves execution budget | Fast, PostgreSQL, cluster | Same turn ID, configured limit, consumed request count and reserved output after recovery. |
| Kill a production worker during streaming | PostgreSQL, cluster | Original run becomes interrupted; inspection does not dispatch another model request or a tool. |
| Resume event cursor | Fast, PostgreSQL, cluster | Tail contains only IDs strictly after the saved cursor, ordered without duplicates. |
| Current and older run results | PostgreSQL, cluster | Crash interruption remains `interrupted` in the service/SDK view, with its original failure reason and run identity; it is not confused with a later completed run. |
| Read from a second API pod | Cluster | Durable session/run references work through the other API instance after worker replacement. |

Source: `runtime/src/runtime_budget_tests.rs`, `runtime/tests/core.rs`,
`store-postgres/tests/worker_recovery.rs`, `deploy/resilience/cluster.mjs`.

## 2. Unknown tool outcomes and no replay

A lost response cannot establish whether an external action happened. We create a
real durable effect, kill its worker before the result is committed, and check
that the harness tells the model the outcome is unknown. A new explicit turn
reads back state; the transport never repeats the action on the model's behalf.

| Case | Level | Assertions |
| --- | --- | --- |
| Crash after tool admission | Fast, PostgreSQL, cluster | Exactly one `tool_outcome_unknown` response for the original call; original side effect occurs once. |
| Reopen twice | Fast | Recovery cannot append duplicate unknown responses. Started/no-result call is removed from the replay queue. |
| Unstarted call | Fast | Queued is distinct from started; recovery does not fabricate an unknown external effect for a call never dispatched. |
| Already completed call | Fast | A durable result is retained; recovery cannot replace it with unknown or replay it. |
| Explicit inspect after recovery | PostgreSQL, cluster | A subsequent model decision reads the original workspace; it does not repeat the write. |
| Approval survives worker death | PostgreSQL | Pending interaction identity and policy bindings remain stable. Session approvals do not spill into sibling sessions. |
| Submission deduplication | PostgreSQL | Repeated submission ID/text/limits returns the original receipt; conflicting text or limits is rejected. |

Source: `runtime/src/store.rs`, `runtime/tests/core.rs`,
`store-postgres/tests/worker_recovery.rs`, `store-postgres/tests/submissions.rs`.

## 3. Shared environment and process ownership

Two cooperating agents may share an execution environment. They must coordinate
paths and ports themselves. These tests establish process continuity and correct
ownership; sharing a sandbox is not a tenant-isolation boundary.

| Case | Level | Assertions |
| --- | --- | --- |
| Independent workspace managers | Fast on Linux | Stopping one manager does not stop another manager's process; foreign handle is rejected. |
| Two sessions, one environment | Cluster | Both session references resolve to the same environment; separate files and ports serve different expected content. |
| Server survives worker replacement | Cluster | Existing handles still resolve and both HTTP endpoints remain healthy after the worker is killed. |
| Stop one server | Fast on Linux, cluster | Its state becomes stopped and its port stops responding; sibling server remains running and healthy. |
| Independent MCP connections | Docker process qualification | Manager handles, HTTP and bounded logs survive attachment disconnect/reconnect. |
| Lost launch reply | Docker process qualification | Caller checks its original known handle and never repeats the launch. |
| Workspace/generation handles | Docker process qualification | A foreign workspace cannot stop an existing process by reusing a handle. Raw OS PIDs are not process handles. |

Source: `runtime/src/coding/processes.rs`,
`deploy/reference/qualify-process-continuity.mjs`, `deploy/resilience/cluster.mjs`.

## 4. Cancellation, expiry and cleanup

Cancelling an agent turn and destroying an execution environment have different
meanings. We test the distinction so a cancelled chat does not unexpectedly kill
a useful server, while bounded processes and deleted environments do not leak
resources.

| Case | Level | Assertions |
| --- | --- | --- |
| Cross-API cancellation | PostgreSQL, cluster | Control issued from another API reaches the owning worker; turn becomes cancelled/interrupted as documented and does not resume autonomously. |
| Cancellation preserves managed server | Cluster | Environment-owned servers keep serving after an unrelated streaming turn is cancelled. |
| Deadline without polling | Fast on Linux, cluster | Short-lived process expires independently of status reads; a delayed descendant cannot write its marker. |
| Explicit shutdown | Fast on Linux | Process group stops, delayed child is killed and bounded log snapshot remains readable. |
| Recovery with no in-memory client | PostgreSQL, cluster | Shutdown retires old-epoch attachment bookkeeping as unknown, allowing normal close/delete without claiming a remote process stopped. |
| Original environment deletion | Cluster | Closing sessions and deleting their original environment removes the original sandbox pod and its remaining server. |
| Unrelated environments | Cluster | Pre-existing sandbox pod identities remain present after test cleanup. |
| Fixture cleanup | PostgreSQL, cluster | Only test-owned containers, generated credentials and service namespace may be removed; failed/unknown receipts remain available for diagnosis. |

Source: `runtime/src/coding/processes.rs`, `store-postgres/tests/worker_recovery.rs`,
`deploy/resilience/run.mjs`, `deploy/resilience/cluster.mjs`.

## 5. Recording, summaries and export

We compare different views of the same run rather than merely checking that a
chart exists. Missing usage and abrupt worker death must remain visible; no
component may invent token counts or complete an unfinished span retrospectively.

| Case | Level | Assertions |
| --- | --- | --- |
| Budget and validation events | Fast, cluster | Typed limit/validation operations correlate with the correct logical turn; queued effects are not executed at the limit. |
| Model request accounting | Fast | Retries, first-output, compaction and reviewer requests are counted by purpose; cumulative usage snapshots are not added twice. |
| Unknown usage | Fast, cluster | Absent provider counters remain unknown, not zero or an estimate attributed to a tool. |
| Recorder outage | PostgreSQL | Recording uses its own connection/pool; an unavailable recorder cannot block or corrupt execution storage. |
| Authorized queries | PostgreSQL | Run search, summaries and timelines obey role/session scope, cursor bounds and query validation. |
| Summaries match timeline | Cluster | Model/tool group call totals match recorded operations for the same fixed timeline cursor. |
| Actual OTLP receiver | Fast, cluster | Exported spans correlate by trace ID, span ID and operation with recorded steps; no duplicate span identity for a completed run. |
| Recovery/cancellation/process events | PostgreSQL, cluster | Recoveries, unknown effects, cancellations, process starts/stops and budget exhaustion have recorded typed events. |
| Content redaction | Fast, cluster | Prompt/tool-argument sentinel and service token do not appear in exported metadata. |
| Export failures and shutdown | Fast | Slow/rejecting collectors respect bounded flush/shutdown; failures are observable. |

Source: `runtime/tests/model_accounting.rs`, `runtime/tests/telemetry.rs`,
`observability/tests/`, `telemetry/tests/`, `server-reference/tests/recording*.rs`.

## 6. Management UI and qualification safety

The browser must show useful run data, and the test runner must be safe to run
beside other development workloads.

| Case | Level | Assertions |
| --- | --- | --- |
| Presentation contracts | Fast | Missing/zero/partial counters differ, parallel timeline intervals remain intact, and untrusted display text is escaped. |
| Exact-run search | Cluster browser | Search by actual run ID returns the expected selectable run. |
| Usage and timeline | Cluster browser | Grouped usage and timeline render from the real service API without page errors; screenshots are retained. |
| Fault target validation | Fast, cluster | Reject cloud/nonlocal contexts, foreign namespace ownership, live-model deployments and incorrect service confirmation. |
| Bounded failures | All | Timeouts produce a failed report, not a fabricated pass; uncertain tool actions are not retried. |
| Private evidence | All | Raw logs, generated credentials and receipts remain in a gitignored private output directory. Public evidence contains aggregate results only. |

Source: `service-ui/test/observability.test.mjs`, `deploy/resilience/contract.test.mjs`,
`deploy/resilience/cluster.mjs`.

## Running the suite

From the repository root, with dependencies already fetched:

```sh
node deploy/resilience/run.mjs fast .ow-service-resilience-fast
```

PostgreSQL worker-death cases require native Linux. On macOS, build the current
source's opt-in Linux qualification image (no production or registry deployment):

```sh
docker build -f deploy/resilience/Dockerfile -t openworker-resilience:test .
export OW_RESILIENCE_LINUX_IMAGE="$(docker image inspect openworker-resilience:test --format '{{.Id}}')"
# Matching Node coding-runner image; see deploy/service/Dockerfile.coding-runner.
export OW_RESILIENCE_RUNNER_IMAGE="$(docker image inspect YOUR_CODING_RUNNER_IMAGE --format '{{.Id}}')"
node deploy/resilience/run.mjs postgres .ow-service-resilience-postgres
```

For Kubernetes, first prepare an explicitly owned local fixture using
`deploy/resilience/prepare-local.mjs`; its configuration names the kind context,
kubeconfig, imported service/runner digests, loopback API port and existing
OpenShell TLS connection. No provider key is needed. See
[local service packaging](../../deploy/service/README.md) for cluster prerequisites.

```sh
node deploy/resilience/prepare-local.mjs .ow-service-resilience-cluster LOCAL_CONFIG.json
# Use the service ID returned by preparation. Playwright must already be installed.
export OW_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright
node deploy/resilience/run.mjs cluster .ow-service-resilience-results \
  .ow-service-resilience-cluster/service EXACT_SERVICE_ID
```

Every invocation needs a **new output directory**. Destructive cluster qualification
also requires a fresh prepared service namespace; a durable marker rejects reruns
against a partially completed or uncertain attempt. Logs and `report.json` identify
which level actually ran. The cluster run retains screenshots and correlation
proof, then removes its owned service namespace only after successful environment
cleanup. On failure it retains the service and original receipts for inspection;
never rerun an uncertain action simply to make a report green.
