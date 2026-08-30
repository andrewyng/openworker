"""Step 5 — built-in tool adapters.

One module per adapter:

- ``metered`` — the pay-per-tool-call adapter (the metered-web broker,
  folded in as a first-class scope). See metered.py.

Later (planned, not v0):
- ``otel``  — an OpenTelemetry *exporter* adapter (the *billing*
  *backend* in metered.py is one *backend*; the *OTel* *exporter* is
  a *second* *backend, with a *different* *Receipt* *shape* (a *span*
  or a *metric*, not a *receipt*). The *adapter* *shape is the same
  (the *protocol* *in metered.py*); the *backend* is the
  *implementation, and it's pluggable per adapter.
"""
