# Size a service deployment

Measure your own application workload before choosing worker counts or instance
sizes. Model latency, tool execution, retained history and database contention
can each become the limiting factor.

## Separate the resource budgets

- **API capacity:** request handling, authentication and concurrent event streams.
- **Worker capacity:** active turns, model connections and session bookkeeping.
- **Execution capacity:** sandbox CPU, memory, storage and tool processes.
- **Database capacity:** connections, retained history, checkpoints and query load.

Adding API replicas does not automatically increase worker or sandbox capacity.
Start with a small configuration and increase one dimension at a time. Compare
queue age, active turn duration, memory, CPU, provider throttling and database
latency. Include replacement/drain capacity during upgrades.

Use warm sessions, realistic histories and the intended optimized build in your
measurements. Include long tool calls and approval waits, not only text-only
requests. Keep tenant quotas, aggregate backlog limits and overload handling in
your application; per-session limits do not replace them.

## Run the bounded load fixture

Use a dedicated local PostgreSQL instance. Record its image and CPU/memory limits.
Do not run other fault tests against the same database during a measurement.

```sh
# From harness/; supply OW_PG_TEST_URL for the isolated database.
OW_RUN_LOAD=1 OW_CONCURRENCY_REPORT_DIR=/absolute/private/reports \
  cargo test --release --locked -p openworker-store-postgres \
  --features test-support --test worker_recovery load::bounded_load_matrix \
  -- --include-ignored --test-threads=1 --nocapture
```

The fixture requires `OW_RUN_LOAD=1`; a generic test-suite pass does not mean that
it ran. Completed cases remove their owned schemas and child processes. Failed
cases may retain diagnostic schemas; inspect ownership before removing them.

Repeat with your actual models, tools and sandbox topology, including provider
throttling and worker/storage failures. Treat unknown tool outcomes as unknown;
a successful retry is not proof that the original operation had no effect.
See [worker draining](DRAINING.md) and [upgrade guidance](UPGRADES.md).
