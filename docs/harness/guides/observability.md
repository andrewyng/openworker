# Observe agent work

Use the management UI to search for a run, inspect its timeline and compare grouped
model/tool activity. Export OpenTelemetry to your collector or supported analysis
service when you need organization-wide dashboards.

Observability and compliance have different jobs. Traces and metrics help explain
latency, usage and failures. The compliance journal records committed policy and
execution metadata with scoped queries and durable export. Do not turn on prompt
or tool-content capture just to obtain usage statistics.

[Observability design](../OBSERVABILITY.md) covers storage and correlation;
[queries](../OBSERVABILITY-QUERIES.md) covers authorized run access;
[guardrails and compliance](../GUARDRAILS-QUICKSTART.md) covers decision records,
retention and SIEM delivery. A missing exporter must not trigger tool replay.
