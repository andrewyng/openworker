import { test } from "node:test";
import assert from "node:assert/strict";
import { allowed } from "../server.mjs";
import {
  duration,
  measurement,
  usageTotal,
  purposeUsage,
  timelineLayout,
  coverageNotes,
  esc,
} from "../public/observability-data.js";
test("recording proxy accepts encoded literal search and exact bounded read routes only", () => {
  for (const p of [
    "/v1/observability/runs?q=Alpha+%25+Game&limit=10",
    "/v1/observability/runs?q=%E6%B8%B8%E6%88%8F&agent=builder%401&after=eyJ2ZXJzaW9uIjoxfQ",
    "/v1/observability/summary?limit=25&from_ms=0&to_ms=253402300799000",
    "/v1/sessions/s_1/observability/t_1/timeline?after=0&through=9223372036854775807&limit=200",
    "/v1/sessions/s_1/observability/t_1/summary?through=0",
    "/v1/observability/health",
  ])
    assert(allowed("GET", p), p);
  for (const p of [
    "/v1/observability/runs?q=%",
    "/v1/observability/runs?q=%FF",
    "/v1/observability/runs?q=%00",
    "/v1/observability/runs?q=" + "a".repeat(201),
    "/v1/observability/runs?q=" + encodeURIComponent("游".repeat(67)),
    "/v1/observability/runs?after=" + "a".repeat(1025),
    "/v1/observability/runs?after=x%2Fy",
    "/v1/observability/runs?limit=101",
    "/v1/observability/runs?q=a&q=b",
    "/v1/observability/runs?session_hash=other",
    "/v1/observability/summary?limit=26",
    "/v1/observability/summary?database=elsewhere",
    "/v1/observability/health?after=1",
    "/v1/observability/runs#secret",
    "/v1/sessions/s%2Fother/observability/t/timeline",
    "/v1/sessions/s/observability/t/timeline?limit=201",
    "/v1/sessions/s/observability/t/timeline?through=9223372036854775808",
    "/v1/sessions/s/observability/t/summary?after=0",
    "/v1/sessions/s/observability/t/timeline?scope=all",
  ])
    assert(!allowed("GET", p), p);
  for (const method of ["POST", "PUT", "DELETE"])
    assert(!allowed(method, "/v1/observability/runs"));
});
test("presentation keeps missing, zero and partial counters distinct", () => {
  assert.equal(duration(null), "—");
  assert.equal(duration(9007199254740992), "—");
  assert.equal(duration(0), "0 ms");
  assert.equal(measurement({ total: 0, known: 1, unknown: 0 }), "0");
  assert.equal(measurement({ total: null, known: 0, unknown: 1 }), "—");
  assert.equal(
    measurement({ total: 20, known: 1, unknown: 1 }),
    "20 + unknown",
  );
  assert.equal(
    usageTotal({ input: { total: 20 }, output: { total: null } }),
    null,
  );
  assert.equal(usageTotal({ input: { total: 0 }, output: { total: 0 } }), 0);
  const missing = purposeUsage([
    {
      purpose: "main",
      usage: {
        input: { total: null, unknown: 1 },
        output: { total: null, unknown: 1 },
      },
    },
  ]);
  assert.equal(missing[0].total, null);
  assert.equal(missing[0].missing, 1);
  assert.equal(
    purposeUsage([
      { purpose: "main", usage: { input: { total: 0 }, output: { total: 0 } } },
    ])[0].total,
    0,
  );
  assert.equal(
    esc('<img src=x onerror="steal()">'),
    "&lt;img src=x onerror=&quot;steal()&quot;&gt;",
  );
});
test("timeline retains parallel intervals and unknown duration markers", () => {
  const r = (cursor, start, duration) => ({
    cursor,
    step: { started_ms: start, duration_us: duration },
  });
  const plot = timelineLayout([
    r(3, 1100, null),
    r(2, 1050, 150000),
    r(1, 1000, 100000),
  ]);
  assert.deepEqual(
    plot.rows.map((r) => r.cursor),
    [1, 2, 3],
  );
  assert.equal(plot.to - plot.from, 200000);
  assert.equal(plot.rows[0].width, 50);
  assert.equal(plot.rows[1].left, 25);
  assert.equal(plot.rows[1].width, 75);
  assert.equal(plot.rows[2].width, 0);
  assert.equal(plot.rows[2].step.duration_us, null);
  assert(
    coverageNotes({
      scanned_steps: 1,
      scan_truncated: true,
      hierarchy_incomplete: true,
      unknown_timing_steps: 1,
    }).some((s) => s.includes("4,096")),
  );
});
