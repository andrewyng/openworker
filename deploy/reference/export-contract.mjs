import assert from "node:assert/strict";
export const attributes = (attrs) =>
  Object.fromEntries(
    (attrs || []).map((a) => [
      a.key,
      a.value?.stringValue ??
        a.value?.intValue ??
        a.value?.doubleValue ??
        a.value?.boolValue,
    ]),
  );
export function collectorRecords(envelopes) {
  const spans = [],
    logs = [],
    metrics = [],
    resources = [];
  for (const e of envelopes) {
    for (const r of e.resourceSpans || []) {
      resources.push(attributes(r.resource?.attributes));
      for (const s of r.scopeSpans || []) spans.push(...(s.spans || []));
    }
    for (const r of e.resourceLogs || [])
      for (const s of r.scopeLogs || []) logs.push(...(s.logRecords || []));
    for (const r of e.resourceMetrics || [])
      for (const s of r.scopeMetrics || [])
        metrics.push(
          ...(s.metrics || []).map((m) => ({
            ...m,
            resource: attributes(r.resource?.attributes),
          })),
        );
  }
  return { spans, logs, metrics, resources };
}
export function compareCollector(recording, wire, registry) {
  const rows = recording.steps.map((r) => r.step),
    byId = new Map(rows.map((s) => [s.span_id, s]));
  assert.equal(wire.spans.length, rows.length);
  assert.equal(new Set(wire.spans.map((s) => s.spanId)).size, rows.length);
  const keys = new Set(registry.attributes.map((a) => a.key)),
    dims = new Set(
      registry.attributes.filter((a) => a.metric_dimension).map((a) => a.key),
    );
  for (const r of wire.resources) {
    assert.equal(r["openworker.semconv.version"], registry.semconv_version);
    assert.equal(r["openworker.schema.version"], registry.version);
    for (const k of Object.keys(r)) assert(keys.has(k), k);
  }
  for (const span of wire.spans) {
    const row = byId.get(span.spanId);
    assert(row, span.spanId);
    const a = attributes(span.attributes);
    for (const k of Object.keys(a)) assert(keys.has(k), k);
    assert.equal(span.traceId, row.trace_id);
    assert.equal(a["openworker.operation"], row.operation);
    assert.equal(a["openworker.outcome"], row.outcome);
    assert.equal(a["openworker.turn.hash"], row.turn_hash);
    assert.equal(a["session.id"], row.session_hash);
    assert.equal(a["gen_ai.conversation.id"], row.session_hash);
    assert(!("gen_ai.request.model" in a));
    assert(!("gen_ai.tool.name" in a));
    const model = row.operation === "model.attempt";
    for (const [key, field] of [
      ["gen_ai.usage.input_tokens", "input"],
      ["gen_ai.usage.output_tokens", "output"],
      ["gen_ai.usage.cache_read.input_tokens", "cache_read"],
      ["gen_ai.usage.cache_creation.input_tokens", "cache_write"],
      ["gen_ai.usage.reasoning.output_tokens", "reasoning"],
    ]) {
      const v = model ? row.usage?.[field] : null;
      if (v == null) assert(!(key in a), key);
      else assert.equal(Number(a[key]), v, key);
    }
    if (model) {
      assert.equal(a["langfuse.observation.type"], "generation");
      assert.equal(span.kind, 3);
    }
    if (row.operation === "model.requested")
      assert.equal(a["langfuse.observation.type"], "event");
    if (row.parent_span_id) assert.equal(span.parentSpanId, row.parent_span_id);
    for (const link of row.links)
      assert(
        span.links?.some(
          (l) => l.spanId === link.span_id && l.traceId === link.trace_id,
        ),
      );
    if (row.operation === "approval.wait")
      assert.equal(
        (BigInt(span.endTimeUnixNano) - BigInt(span.startTimeUnixNano)) / 1000n,
        BigInt(row.duration_us),
      );
  }
  assert.equal(wire.logs.length, rows.length);
  for (const log of wire.logs) {
    for (const k of Object.keys(attributes(log.attributes)))
      assert(keys.has(k), k);
    assert(!log.body || Object.keys(log.body).length === 0);
  }
  for (const m of wire.metrics)
    for (const series of [m.sum, m.gauge, m.histogram].filter(Boolean))
      for (const p of series.dataPoints || [])
        for (const key of Object.keys(attributes(p.attributes)))
          assert(dims.has(key), key);
  // Keep the newest cumulative sample per process/dimension stream.
  const latest = new Map();
  for (const m of wire.metrics.filter(
    (m) => m.name === "openworker.model.tokens",
  ))
    for (const p of m.sum.dataPoints) {
      const dims = attributes(p.attributes);
      const key = JSON.stringify([
        m.resource["service.instance.id"],
        Object.entries(dims).sort(),
      ]);
      const previous = latest.get(key);
      if (!previous || BigInt(p.timeUnixNano) >= BigInt(previous.time))
        latest.set(key, {
          time: p.timeUnixNano,
          direction: dims.direction,
          value: Number(p.asInt ?? p.asDouble),
        });
    }
  const totals = { input: 0, output: 0 };
  for (const p of latest.values()) totals[p.direction] += p.value;
  assert.deepEqual(totals, { input: 110, output: 22 });
  const generations = rows.filter((s) => s.operation === "model.attempt");
  return {
    steps: rows.length,
    modelRequests: generations.length,
    input: generations.reduce((n, s) => n + (s.usage?.input ?? 0), 0),
    output: generations.reduce((n, s) => n + (s.usage?.output ?? 0), 0),
    resources: wire.resources.length,
  };
}
export function compareLangfuse(observations, recording) {
  const rows = recording.steps.map((r) => r.step),
    byId = new Map(rows.map((r) => [r.span_id, r]));
  assert.equal(observations.length, rows.length);
  const generations = observations.filter(
    (o) => o.type.toLowerCase() === "generation",
  );
  assert.equal(generations.length, 4);
  let total = 0;
  const unknown = [];
  for (const o of observations) {
    const r = byId.get(o.id);
    assert(r, o.id);
    assert.equal(o.traceId, r.trace_id);
    assert.equal(o.sessionId, r.session_hash);
    assert.equal(o.name, r.operation);
    assert.equal(o.parentObservationId ?? null, r.parent_span_id ?? null);
    assert(!o.input && !o.output);
    assert.equal(o.level, r.outcome === "error" ? "ERROR" : "DEFAULT");
    assert.equal(
      o.statusMessage || "",
      r.outcome === "error" ? "operation_failed" : "",
    );
    if (r.operation !== "model.attempt") {
      assert.notEqual(o.type.toLowerCase(), "generation");
      assert.deepEqual(
        o.usageDetails ?? {},
        {},
        "Usage belongs only to model requests",
      );
      continue;
    }
    const u = o.usageDetails ?? {};
    if (r.usage?.input == null) {
      assert.deepEqual(u, {}, "Unreported usage must not become known zero");
      unknown.push({ keys: Object.keys(u), total: u.total ?? null });
      continue;
    }
    // Langfuse's own OTLP ingestion converts inclusive cache counts into exclusive
    // buckets. This assertion checks the vendor, not a local emulation of it.
    const cache = (r.usage.cache_read ?? 0) + (r.usage.cache_write ?? 0);
    assert.equal(u.input ?? 0, r.usage.input - cache);
    assert.equal(u.input_cached_tokens ?? 0, r.usage.cache_read ?? 0);
    assert.equal(u.input_cache_creation ?? 0, r.usage.cache_write ?? 0);
    assert.equal(
      u.output ?? 0,
      (r.usage.output ?? 0) - (r.usage.reasoning ?? 0),
    );
    assert.equal(u.output_reasoning_tokens ?? 0, r.usage.reasoning ?? 0);
    assert.equal(u.total, r.usage.input + (r.usage.output ?? 0));
    total += u.total;
  }
  assert.equal(total, 132);
  return {
    observations: observations.length,
    generations: generations.length,
    knownTokenTotal: total,
    unknownUsageTransform: unknown,
  };
}
