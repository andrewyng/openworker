// Read-only reconciliation of a real UI-submitted local run. Never submits,
// retries or replays an agent/tool action. State/credentials remain gitignored.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import https from "node:https";
import { createHash } from "node:crypto";
const root = resolve(process.argv[2] || "");
assert(process.argv[2], "Usage: qualify-live-observability.mjs STATE");
const journal = JSON.parse(await readFile(join(root, "run.json"))),
  run = journal.verificationRun;
assert(
  run?.session_id && run.turn_id,
  "A journaled completed UI run is required",
);
const m = JSON.parse(await readFile(join(root, "service/kubernetes.json"))),
  lf = JSON.parse(await readFile(join(root, "langfuse/installation.json"))),
  c = JSON.parse(
    await readFile(join(root, "langfuse/private/credentials.json")),
  );
const token = (
    await readFile(
      process.env.OW_SERVICE_TOKEN_FILE ||
        join(root, "service/input/admin-token"),
      "utf8",
    )
  ).trim(),
  ca = await readFile(join(root, "service/input/api-ca.crt"));
async function api(path) {
  return new Promise((ok, no) => {
    const r = https.get(
      "https://127.0.0.1:58450" + path,
      {
        ca,
        headers: {
          authorization: "Bearer " + token,
          "x-openworker-service-id": m.id,
          "x-openworker-namespace": "local",
        },
      },
      (res) => {
        let text = "";
        res.on("data", (b) => (text += b));
        res.on("end", () => {
          try {
            assert.equal(res.statusCode, 200);
            ok(JSON.parse(text));
          } catch (e) {
            no(e);
          }
        });
      },
    );
    r.setTimeout(10000, () => r.destroy(Error("Read deadline")));
    r.on("error", no);
  });
}
const result = await api(`/v1/sessions/${run.session_id}/runs/${run.turn_id}`);
assert.equal(result.status, "completed");
const timeline = await api(
    `/v1/sessions/${run.session_id}/observability/${run.turn_id}/timeline?limit=200`,
  ),
  summary = await api(
    `/v1/sessions/${run.session_id}/observability/${run.turn_id}/summary`,
  );
assert.equal(
  timeline.page.next,
  null,
  "Fixture must fit one bounded source page",
);
const rows = timeline.page.steps.map((r) => r.step);
assert(rows.length > 0);
const models = rows.filter((r) => r.operation === "model.attempt"),
  tools = rows.filter((r) => r.operation === "tool.execute");
assert.equal(models.length, 3);
assert.equal(tools.length, 2);
assert.deepEqual(tools.map((r) => r.tool).sort(), ["read_file", "run_shell"]);
const trace = models[0].trace_id;
assert(models.every((r) => r.trace_id === trace));
const input = models.reduce((n, r) => n + r.usage.input, 0),
  output = models.reduce((n, r) => n + r.usage.output, 0);
assert.equal(summary.summary.usage.input.total, input);
assert.equal(summary.summary.usage.output.total, output);
let observations;
const until = Date.now() + 120000;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
// Keep Node's otherwise unreferenced abort/deadline timers alive while waiting
// for the vendor's async ingestion queue. All retries here are reads only.
const lifetime = setInterval(() => {}, 1000);
try {
  do {
    const q = new URLSearchParams({
      traceId: trace,
      limit: "1000",
      fields: "core,basic,usage,io,model,metadata",
      fromStartTime: new Date(run.created_ms - 1000).toISOString(),
      toStartTime: new Date().toISOString(),
    });
    const r = await fetch(
      `http://127.0.0.1:${lf.port}/api/public/v2/observations?${q}`,
      {
        headers: {
          authorization:
            "Basic " +
            Buffer.from(c.publicKey + ":" + c.secretKey).toString("base64"),
        },
        signal: AbortSignal.timeout(10000),
      },
    );
    assert.equal(r.status, 200, "Langfuse read failed");
    const v = await r.json();
    observations = v.data;
    assert(Array.isArray(observations));
    if (rows.every((row) => observations.some((o) => o.id === row.span_id)))
      break;
    await wait(1000);
  } while (Date.now() < until);
} finally {
  clearInterval(lifetime);
}
const byId = new Map(observations.map((o) => [o.id, o]));
assert(
  rows.every((r) => byId.has(r.span_id)),
  "Vendor has not indexed every source record",
);
const sessionHash = createHash("sha256").update(run.session_id).digest("hex");
let vendorInput = 0,
  vendorOutput = 0;
for (const row of rows) {
  const o = byId.get(row.span_id);
  assert.equal(o.traceId, trace);
  assert.equal(o.sessionId, sessionHash);
  assert.equal(o.parentObservationId ?? null, row.parent_span_id ?? null);
  assert(!o.input && !o.output, "Content capture must stay off");
  if (row.operation === "model.attempt") {
    assert.equal(o.type, "GENERATION");
    const u = o.usageDetails;
    const cached = (row.usage.cache_read ?? 0) + (row.usage.cache_write ?? 0),
      reasoning = row.usage.reasoning ?? 0;
    assert.equal(u.input ?? 0, row.usage.input - cached);
    assert.equal(u.input_cached_tokens ?? 0, row.usage.cache_read ?? 0);
    assert.equal(u.input_cache_creation ?? 0, row.usage.cache_write ?? 0);
    assert.equal(u.output ?? 0, row.usage.output - reasoning);
    assert.equal(u.output_reasoning_tokens ?? 0, reasoning);
    assert.equal(u.total, row.usage.input + row.usage.output);
    vendorInput += (u.input ?? 0) + cached;
    vendorOutput += (u.output ?? 0) + reasoning;
  } else {
    assert.notEqual(o.type, "GENERATION");
    assert.deepEqual(o.usageDetails ?? {}, {});
  }
}
assert.equal(vendorInput, input);
assert.equal(vendorOutput, output);
const encoded = JSON.stringify(observations);
for (const privateText of [
  "Final read-only verification",
  "hello-observability.md",
  "# Dual UI check",
])
  assert(!encoded.includes(privateText), "Unexpected exported content");
await writeFile(
  join(root, "verified-source-timeline.json"),
  JSON.stringify(timeline, null, 2),
  { mode: 0o600 },
);
await writeFile(
  join(root, "verified-source-summary.json"),
  JSON.stringify(summary, null, 2),
  { mode: 0o600 },
);
await writeFile(
  join(root, "verified-langfuse-observations.json"),
  JSON.stringify(observations, null, 2),
  { mode: 0o600 },
);
const report = {
  version: 1,
  at: new Date().toISOString(),
  passed: true,
  scope:
    "real live-model agent, local Kubernetes + OpenShell; source and vendor reads",
  sessionId: run.session_id,
  runId: run.turn_id,
  traceId: trace,
  sourceRecords: rows.length,
  vendorRecordsInTrace: observations.length,
  matchedSourceRecords: rows.length,
  modelRequests: models.length,
  tools: tools.map((r) => r.tool),
  inputTokens: input,
  outputTokens: output,
  contentCapture: "metadata only; no prompts, arguments or results",
  model: "claude-sonnet-5",
  managementUrl: "http://127.0.0.1:58650/#observability",
  langfuseUrl: `http://127.0.0.1:${lf.port}/project/${lf.projectId}/traces`,
  checks: [
    "UI-submitted run completed, actual sandbox file independently verified",
    "source model/tool/turn records from catalog service-worker",
    "all source span IDs, parent IDs, trace and pseudonymous session match vendor",
    "three actual model requests reconcile inclusive/exclusive usage",
    "no prompts or file/tool payloads in vendor records",
  ],
  limits: [
    "Earlier trials retain their recording/export gaps; no automatic backfill or agent replay",
    "Vendor records include additional correlated storage/lifecycle spans",
    "Local integration settings are not a production Langfuse sizing recommendation",
  ],
};
await writeFile(
  join(root, "qualification.json"),
  JSON.stringify(report, null, 2),
  { mode: 0o600 },
);
console.log(JSON.stringify(report));
