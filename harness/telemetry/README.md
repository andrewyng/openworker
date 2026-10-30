# Optional harness telemetry

Instance-owned OpenTelemetry traces, metrics and lifecycle logs. Disabled by
default; no global subscriber/provider or default vendor destination. The server
reference composes export with optional [local recording](../observability/README.md).
Recording has its own bounded writer and independently configurable SQLite or
PostgreSQL connection. Enabling recording does not enable remote metadata/content.
Neither system is a lossless billing ledger or security audit log.

## Configure an exporter

Add `telemetry` to the server-reference JSON configuration:

```json
{
  "telemetry": {
    "endpoint": "http://127.0.0.1:4318",
    "deployment": "development"
  }
}
```

Use a collector **base URL**, optionally with a path. The exporter appends
`/v1/traces`, `/v1/metrics` and `/v1/logs`. HTTPS is required except loopback HTTP.
URL credentials, queries, fragments and signal-specific endpoint paths reject at
startup. Unreachable exporters do not block agent execution or service readiness.
Authenticated health responses expose local queue/export-failure counters.

For Langfuse, select traces only. Configure authentication using a named
environment variable containing `Basic <base64(public-key:secret-key)>`:

```json
{
  "telemetry": {
    "endpoint": "https://cloud.langfuse.com/api/public/otel",
    "deployment": "production",
    "signals": ["traces"],
    "acknowledgement": "langfuse_v4",
    "headers": [
      {"name": "Authorization", "value_env": "OW_TELEMETRY_AUTH"},
      {"name": "x-langfuse-ingestion-version", "value": "4"}
    ]
  }
}
```

Supply the secret through the application's secret manager or deployment secret;
never put it in JSON, a bundle, URL or process argument. Header values are resolved
at startup; rotating them requires restarting the exporter. Nonsecret protocol
headers can use `value`. Authentication headers require `value_env`. Do not reuse
model/service credentials. Langfuse ingests traces; send all signals to an OTel
collector when you also need metrics/logs and configure its destinations separately.
See [Langfuse's OTLP integration](https://langfuse.com/integrations/native/opentelemetry).

The transport has verified TLS, no redirects/ambient proxies, no inherited OTEL
headers or automatic resource detection, no exporter retries, a three-second HTTP
deadline and a 64-KiB response cap. Custom CA configuration is not exposed; use a
trusted local collector or app-owned components when required. Binary and declared
JSON OTLP acknowledgements are validated. `acknowledgement: "langfuse_v4"` explicitly
accepts Langfuse v4's identified ingestion-job receipt as well as OTLP responses;
it requires traces-only export. The default `"otlp"` rejects that vendor envelope. Partial rejection, warnings, malformed
or oversized acknowledgements and non-success HTTP responses count as failed
batches. Receiver error text is never copied into diagnostic errors. HTTP success
is not proof that a downstream vendor has durably indexed the data.

## Attribute and privacy contract

[attributes.json](attributes.json) is the machine-readable registry for exported
keys, types, capture classes and allowed metric dimensions. Its schema is version
1, with a selected OTel GenAI **1.41.0** attribute subset; this is not a claim of
complete or latest GenAI semantic-convention conformance. The generator produces
checked-in [Rust constants](src/attributes.rs):

```sh
python3 harness/telemetry/scripts/generate-attributes.py --check
```

Python is a development-time generator only; building/shipping the Rust harness
requires no Python runtime. The registry can later generate constants for other
languages; the Python harness is unchanged.

With default `content.capture: "off"`, exports contain fixed operations/outcomes,
hashed identities, timestamps/durations, numeric usage and derived operation/type
hints. `session.id` and `gen_ai.conversation.id` contain the same SHA-256 session
hash as `openworker.session.hash`. These are pseudonymous, not anonymous. Raw
session IDs, prompts, messages, reasoning, paths, URLs, credentials and model/tool
names are excluded. Error status text is constant, never an exception string.

`"content": {"capture": "metadata"}` opts into registered, validated model/tool
identifiers and sandbox/policy metadata. `"full"` also admits registered payload
keys, bounded by `max_bytes` and optional best-effort secret redaction. Full capture
is sensitive and redaction cannot guarantee secret removal. Unknown keys, reserved
typed keys, URL/path-shaped metadata and repeated-attribute growth are rejected.
Recording metadata and remote capture remain independently controlled.

The standard cache-creation key is `gen_ai.usage.cache_creation.input_tokens`.
M5 replaces the old nonstandard `gen_ai.usage.cache_write.input_tokens` wire key;
local recording's `cache_write` field is unchanged. Consumers of the old wire key
must update their queries. Model names and content compatibility keys retain their
existing names; no full migration to structured GenAI message events is claimed.
See the [pinned convention](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-spans.md).

## Counting and context

| Operation | Meaning |
|---|---|
| `api.request` / `command.admission` | HTTP response preparation or committed command admission, not SSE lifetime or applied approval. |
| `agent.execute` | An execution segment of a logical turn; parking ends the segment. |
| `model.requested` | Start marker/event; no completed request usage. |
| `model.attempt` | One main/reviewer/compaction provider request, including retries and failed streams. |
| `tool.execute` | Admitted execution; missing effects/result remain unknown. |
| `policy.review` | Authorization/reviewer path, including waiting/denied outcomes. |
| `approval.*` / `question.*` | Committed transitions and measured human-wait intervals. |
| `environment.operation` | Execution-environment preparation, not complete provider lifecycle coverage. |
| `storage.call` / `worker.*` / `session.recovery*` | Bounded storage, worker ownership and committed recovery activity. |

Usage belongs only to completed model-request records. Parent/turn/tool spans do
not repeat it. Known zero remains zero; unknown stays absent. Input/output totals
include cache/reasoning subsets, which must not be added again. Cache creation,
cache read and reasoning counts remain optional. Each attempt has its own identity
and main/auto-approver/compaction purpose. Vendor ingestion may normalize counters;
compare vendor totals with local recording before using them for reporting.

Models are CLIENT spans with `gen_ai.operation.name=chat` and
`langfuse.observation.type=generation`, even when names/content are off. Start
markers are events. Derived vendor hints cannot be overridden by string metadata.
Stable turn context survives durable parking/resume and worker replacement;
segments have distinct span IDs and explicit links. Task-local child context
requires explicit propagation. Session/turn hashes correlate auxiliary lifecycle
activity without inventing parentage. Invalid-clock intervals stay visible as
unknown timing in recording/logs and do not manufacture invalid-duration spans.

## Metrics and bounds

Metric dimensions are limited to operation, outcome, model purpose and token
direction. No model/tool name, prompt, user/session ID or trace ID is a metric
dimension. Resources include explicit deployment/role, generated process instance,
service version and registry/convention versions. Keep cumulative samples separate
by process instance; do not sum repeated snapshots from the same stream.

- `openworker.operation.count` / `.duration`: completed work and active seconds;
  transition events have counts, not duration samples.
- `openworker.model.tokens`: known input/output per model request and purpose.
- `openworker.model.retry.count`: attempts numbered above one.
- `openworker.model.first_output.duration`: first visible output latency, including
  transport/queue/preparation; not provider-internal generation time.
- `openworker.telemetry.dropped` / `.export_failures`: local drop and failed-batch
  snapshots, not exact HTTP request counts or a durable global ledger.
- Fixed server gauges and last model/tool error timestamps support operational
  charts; see [operations guidance](../../deploy/reference/OPERATIONS.md).

The execution queue holds up to 2,048 messages plus 128 in flight. The trace/log
processors also cap queues at 2,048 and batches at 128. Producers never wait for
receivers. Full queues drop visibly. No per-session in-memory span registry,
durable export spool or automatic agent/tool retry is introduced. All admitted
records are sampled; configurable sampling is not implemented.

Shutdown fences producers and drains within a total budget (normally five seconds).
An in-flight batch may complete later; a custom blocking sink cannot be forcibly
killed. Recording commits survive restart, but queues/crashes/outages can lose
observations. Missing telemetry never authorizes replay of an uncertain tool.

## Embedded integration

The core crate has no OTel SDK/exporter dependency unless `otel`/`otlp` is enabled.
Existing runtime/stdio/Harness SDK defaults stay off; no new high-level SDK API.
Trusted hosts can pass `Telemetry` to `Runtime::open_instrumented` and PostgreSQL
`Config.telemetry`. `otlp::Config::start(role)` builds instance-owned exporters;
construct it on a blocking thread when embedding in Tokio.

`otel::OtelSink::new` accepts app-owned processors/providers. It calls completed
spans' `on_end`, not global sampler/on_start hooks. Keep ownership of shared
providers: the sink flushes but does not explicitly shut them down. Official
Rust OTel components are pinned to 0.33.1. Custom sinks own any extra fields they add.

## Reproduce qualification

See [export qualification](../../deploy/reference/EXPORT-QUALIFICATION.md) for the
pinned local collector/Langfuse fixture, fault tests, recording reconciliation and
limits. The fixture uses synthetic measurements, no paid models or real tools.
Fresh-cloud, Windows VM and multi-AZ qualification remain separate work.
