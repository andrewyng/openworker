# Optional turn traces and evaluations

The high-level recording, query and evaluation APIs are an unpublished source
candidate, not available in the currently published 0.1.0-beta.2 runtime. Use them
only when the supplied SDK declarations and runtime capabilities both support
`recording` (and `evaluations`/`evaluation_export` as needed). Otherwise use existing
`model_usage` events or configured OTLP; do not invent a compatibility fallback.

For a supported candidate:

- Configure `Harness.start({recording:{database:{type:'sqlite',path:absolutePrivatePath}}})`
  only if local trace retention is wanted. Keep it outside model-accessible workspaces.
  SQLite execution and recording are different files. PostgreSQL uses a separate
  `connectionEnv`/schema and explicit trusted migration.
- Read `harness.observability.listTurns`, `timeline`, `summary`, `children` or
  `subtreeSummary`. Follow bounded cursors; unknown usage is not zero. Cache and
  reasoning tokens are subsets. Subtree includes spawned initial tasks, not every
  later message to a child. Never sum overlapping durations as elapsed time.
- `langfuseTelemetry(endpoint, authorizationEnv)` configures metadata-only v4 OTLP
  traces. Keep credentials in the trusted process. Do not double-export one stream.
- `recording.evaluations:true` enables durable `recordEvaluation` results. Same ID
  plus same payload is idempotent; corrections use a new ID and `supersedes`.
  `skipped`/`error` results carry no quality score. Evaluators are app-owned.
- `recording.evaluationSinks` enables a durable score-only queue. It never retries
  agent actions. Reuse original result timestamps and stable IDs across retries.
  Explanations are not exported unless selected. Do not combine this queue with
  a second automatic score exporter to the same destination.
- `recording.evidence` is a separate content opt-in. The authorized app can request
  bounded committed output/tool projections. Never expose the trusted host query
  API to browsers without session ownership and separate content authorization.
- The optional trace-viewer source package accepts an app adapter; do not add a
  UI framework or database to a lightweight app just for instrumentation.

On export failure, report diagnostics and retain durable results as configured.
Never replay a tool or turn to recreate missing telemetry. Preserve required
compliance policy; best-effort traces do not replace the audit journal.
