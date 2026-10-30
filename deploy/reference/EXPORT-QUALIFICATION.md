# Local collector and Langfuse qualification

For the retained Kubernetes installation and a real UI-submitted model/tool run,
see [Persistent local Langfuse](LANGFUSE-LOCAL.md). The fixture below remains
disposable and synthetic.

This fixture sends the **same synthetic turn measurements** to local SQLite,
a pinned official OTel collector, and optionally a pinned real self-hosted
Langfuse v4 installation. It reads the collector's exported records and Langfuse's
observations API to reconcile usage/classification, rather than accepting an HTTP
200 as sufficient evidence. It invokes no live model or real tool.

## Run

Use Docker Desktop/Engine and Node 20+, with a Rust toolchain matching the repo.
Allow roughly 5 GiB of Docker memory for Langfuse plus existing workloads, and
roughly 5 GiB for cached vendor images. Collector-only testing is much smaller.
Images and dependency services are pinned in
[export-qualification-images.json](export-qualification-images.json).

```sh
cargo build --locked --manifest-path harness/Cargo.toml \
  -p openworker-observability --features otlp --example export_probe

# Explicitly pull only the fixture's pinned images. No latest tags.
node --input-type=module -e 'import fs from "node:fs"; import {execFileSync} from "node:child_process"; for(const image of Object.values(JSON.parse(fs.readFileSync("deploy/reference/export-qualification-images.json")))) execFileSync("docker",["pull",image],{stdio:"inherit"});'

# Choose a new, gitignored output directory each time.
node deploy/reference/qualify-exports.mjs .ow-service-export-check --langfuse
# Omit --langfuse for collector-only qualification.
```

The helper refuses reused/unignored state, requires cached digest-pinned images,
binds ingress to loopback, uses unique owner labels and journals intended resources.
It creates disposable PostgreSQL, ClickHouse, Redis and MinIO dependencies only
for the vendor test. Dependencies use an internal network. Docker Desktop requires
an additional bridge for loopback-published web/collector ingress; those two
containers have that bridge, vendor telemetry/AI are disabled, and no real
application, cloud or model credentials are provided. This is not a production
Langfuse deployment or an assertion of complete network isolation.

Generated fixture secrets/configuration are private and removed on normal
completion/failure. Container logs and diagnostic records stay in the ignored,
private output directory; they can contain **fixture-only** service keys. Do not
publish those logs. `qualification.json` contains sanitized results/image pins,
probe binary hash and cleanup status. If the host/process is forcibly terminated,
use `ownership.json` to inspect exact names and matching
`openworker.export-qualification=<id>` labels before removing resources. Never
prune unrelated containers, volumes, networks or caches.

## Assertions

The probe produces 13 facts across two worker/exporter instances in one logical
turn: working-agent error/retry, a known-zero auto-approver, unknown-usage
compaction, four model-start markers, a policy check, two execution segments,
a measured human wait and an unknown-outcome tool with an output-size observation.

- Local recording and collector identities, parents, resume links, hashes, usage,
  operation types, wait timing and all registered attribute keys reconcile.
- Completed requests alone carry usage. Input/output total 110/22, including the
  failed request; cache/reasoning are subsets, not extra totals. Known zero and
  absent usage remain distinct in source facts.
- Metrics are reconciled from the newest cumulative sample per process/dimension;
  repeated snapshots are not added. All dimensions belong to the fixed registry.
- Default-off export excludes model/tool names and payload sentinels. Model
  completions map to generations; start markers do not. Hashed session correlation
  remains available without publishing a raw session ID.
- Langfuse is authenticated using generated keys, with v4 ingestion and v2
  observation reads. Its queue receipt requires explicit `langfuse_v4`
  acknowledgement mode. Standard OTLP keeps its own strict response handling.
  Vendor normalization is reported separately from source-known/unknown facts.
- Exact-owned containers/anonymous volumes and network are removed. No unrelated
  service, database, cluster or cloud resource is changed.

See [telemetry configuration](../../harness/telemetry/README.md) and the shared
[attribute registry](../../harness/telemetry/attributes.json). The registry pins
selected GenAI 1.41.0 attributes, not all evolving GenAI conventions. Langfuse
semantics are checked against the pinned vendor images; future releases must be
qualified again.

## Other gates

```sh
python3 harness/telemetry/scripts/generate-attributes.py --check
cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-telemetry -p openworker-observability \
  -p openworker-server-reference --all-features --tests
cargo run --locked --manifest-path harness/Cargo.toml \
  -p openworker-observability --example recording_probe -- .ow-service-recording-throughput
```

Fault tests exercise authentication rejection, redirects, partial/warning,
malformed/oversized acknowledgements, vendor versus standard response dialects,
ambient header/resource/proxy isolation, queue pressure and bounded shutdown.
Recording/export failure tests retain independent execution storage and do not
retry tools. The small recording probe measures synthetic enqueue/SQLite drain
throughput; it is not an agent-capacity benchmark or an overhead percentage.
No new SQL batching is introduced without a representative bottleneck measurement.

Native Linux worker recovery and separately configured PostgreSQL recording
connections use [qualify-observability.mjs](qualify-observability.mjs) and
[Dockerfile.observability](Dockerfile.observability). These remain separate from
vendor UI/cloud, Windows and multi-AZ production acceptance.

Primary references: [OTel 1.41 GenAI spans](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-spans.md),
[Langfuse OTLP](https://langfuse.com/integrations/native/opentelemetry),
[token normalization](https://langfuse.com/docs/observability/features/token-and-cost-tracking),
[observations API](https://langfuse.com/docs/api-and-data-platform/features/observations-api),
[self-hosting](https://langfuse.com/self-hosting/deployment/docker-compose).
