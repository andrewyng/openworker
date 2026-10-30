# Querying agent activity

A run is one submitted turn within a session. It can contain many model requests,
tool calls, compactions, approvals and execution segments. These read APIs use
service-token authorization and the canonical execution database; recording data
never grants access to a run.

## Find a run

`GET /v1/observability/runs` accepts:

| Parameter | Meaning |
| --- | --- |
| `q` | Literal substring of session name, session ID, turn ID or submission ID; max 200 bytes. Prompts are not searched. |
| `agent` | Exact coworker ID. |
| `from_ms`, `to_ms` | Inclusive UTC submission timestamps in milliseconds. |
| `limit` | 1–100; default 25. |
| `after` | Opaque `next_cursor` from the preceding page. Keep the same filters and credential. |

```sh
# Use a private curl configuration containing the service token and
# x-openworker-namespace header. Keep credentials out of shell history/argv.
curl --config "$OWS_CURL_CONFIG" --get \
  "$OWS_SERVICE_URL/v1/observability/runs" \
  --data-urlencode 'q=Deployment diagnosis' --data 'limit=25'
```

The response contains `runs`, `next_cursor` and `as_of_us`. Each run has
`session_id`, `turn_id`, `submission_id`, `name`, `agent`, `model`, `status` and
`created_ms`. Newest submissions come first, with stable tie breakers. Search
applies owned/explicit-session/all scope in SQL before LIMIT, and works when
recording storage is disabled or unavailable. Cursors are pagination markers,
not reusable authorization grants.

## Read the timeline

`GET /v1/sessions/{session_id}/observability/{turn_id}/timeline`

Pass `limit` (1–200; default 100), then continue with `after=page.next` and the
same `through=page.through`. Stop when `page.next` is null. `page.steps` contains
metadata-only immutable records, ordered by recording cursor. Timestamps allow
clients to arrange events chronologically; a completed human wait may arrive
later with its original start timestamp.

`through` bounds the read. It is **not** a frozen snapshot across HTTP requests:
late commits and retention can change earlier pages. Refresh from the beginning
when rebuilding a view; do not cache a summary solely by its high-water cursor.
Responses send `Cache-Control: no-store`.

## Summarize a run

`GET /v1/sessions/{session_id}/observability/{turn_id}/summary`

An optional `through` bounds the raw records. The response contains canonical
run metadata and `summary` (projection version 1), freshly rebuilt from at most
4,096 steps. No materialized cache or extra database service is required.

* `usage` contains input/output/cache-read/cache-write/reasoning measurements.
  Each measurement has `total`, `known`, `unknown` and `overflow`. `total` sums
  **reported** values; missing usage does not become zero. Cache/reasoning are
  subsets, not additional input/output tokens. Usage belongs only to completed
  `model.attempt` records, including failed attempts with reported usage.
* `models` groups requests by model and purpose; `tools` groups actual executions
  by tool name. Each group includes calls, outcomes and accumulated duration.
  Models include first-output measurements; tools include returned-result bytes.
  A denied authorization is not counted as an executed tool.
* `active_union_us` merges overlapping intervals within a group. It differs from
  accumulated duration: two overlapping model requests can total 200 ms of work
  within 150 ms of observed time. Individual group lists are capped at 128;
  `group_truncated` means they cannot be summed into the overall total.
* `exclusive_observed_us` partitions the observed window into model, tool, policy,
  approval/question wait, other active time, parallel work and unobserved gaps.
  Policy and execution-segment spans are containers: reviewer time nested within
  policy is counted once, using recorded parent relationships. Independent policy
  work in another run remains parallel work. Missing/cyclic hierarchy is explicit.
  Concurrent primary phases use a `parallel` bucket.
  Gaps are not assigned to model/tool work. Timing uses recorded millisecond
  coordinates plus monotonic span durations; it is not GPU inference timing.
* `wall_elapsed_us` is separate from those sums. It is present only for a terminal
  run with all expected closed segments, its final segment and a usable canonical
  start timestamp. Unfinished/missing spans do not acquire fabricated durations.

`coverage` reports scan/group truncation, expected/recorded segments, pending waits
from canonical state, unmatched open requests, completions without start facts,
unknown timing/outcomes and clock/range anomalies. Its guarantee is always
`best_effort`. Closed visible segments are not proof of lossless token billing.
Historical records without turn correlation are not backfilled into a total.

## Group several runs

`GET /v1/observability/summary` accepts the run-search filters and cursor, with a
maximum page size of 25 runs and a shared 4,096-step budget. The response includes
`selection`, `selected_runs`, `runs_with_records`, `selection_truncated`, and a
combined `summary`. This is explicitly a `selected_run_page` scope, not an
unbounded deployment total. Runs without records remain visible in the selection.

There is no single run wall elapsed across concurrent sessions. Reported usage
and accumulated work can be combined across disjoint record sets; interval unions
and exclusive time must not simply be added across overlapping selections.

## Access, storage and failures

All recording reads first authorize the canonical session/turn, then recheck
current permission before returning results. Revocation during a blocked recording
read rejects the response. The caller cannot choose deployment/schema, connection
settings or identity hashes. Every service request retains the namespace boundary.

Four blocking reader jobs per API instance use independent read-only PostgreSQL
transactions. Disconnecting a client does not free a permit while its SQL still
runs. Rows, groups, cursors and statements are bounded. No query launches agent
work or retries a tool action. Existing security-denial auditing still applies.

| Status | Meaning |
| --- | --- |
| 400 | Invalid bounds, cursor/filter mismatch or unsupported parameters. |
| 401 / 403 | Missing, expired/revoked credential or insufficient run scope. |
| 404 | An authorized lookup has no canonical run. |
| 429 | All four recording readers are occupied. |
| 503 `recording_unavailable` | Recording is disabled/unavailable; no fallback into execution storage. |

Run the normal explicit migration before deployment to add the optional query
indexes and recording schema. Execution/recording SQL layouts remain unchanged;
JSON records are still v1/v2. Large-table index rollout, lost-record recovery,
materialized rollups, and live management UI integration are separate work.
