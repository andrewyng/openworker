// Explicit local Grafana + isolated PG schema. Never touches a live app schema.
// Run after building harness/server-reference/examples/operations_probe.
import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import assert from "node:assert/strict";
import { load, compose, until, repo } from "./stack.mjs";
import { write as provision } from "./operations.mjs";

const root = resolve(process.argv[2] ?? ".ow-stack");
const m = await load(root);
if (!m.observability || !process.env.OW_PG_TEST_URL)
  throw Error(
    "Existing local observability state and isolated OW_PG_TEST_URL required",
  );
const schema = `ow_ops_${randomUUID().replaceAll("-", "")}`;
const binary = join(repo, "harness/target/debug/examples/operations_probe");
const env = {
  ...process.env,
  OW_OPS_SCHEMA: schema,
  OW_OPS_ENDPOINT: `http://127.0.0.1:${m.ports.otlp}`,
};
const auth =
  "Basic " +
  Buffer.from(
    "admin:" +
      (await readFile(join(root, "private/grafana-password"), "utf8")).trim(),
  ).toString("base64");
const api = async (path, options = {}) => {
  const r = await fetch(`http://127.0.0.1:${m.ports.grafana}${path}`, {
    ...options,
    headers: { Authorization: auth, "Content-Type": "application/json" },
    signal: AbortSignal.timeout(5000),
  });
  if (!r.ok) throw Error(`Grafana ${path.split("?")[0]} failed: ${r.status}`);
  return r.json();
};
async function setDatasourceURL(url) {
  const current = await api("/api/datasources/uid/prometheus");
  return api("/api/datasources/uid/prometheus", {
    method: "PUT",
    body: JSON.stringify({ ...current, url }),
  });
}
const query = async (expr) =>
  (
    await api(
      "/api/datasources/proxy/uid/prometheus/api/v1/query?query=" +
        encodeURIComponent(expr),
    )
  ).data.result;
const children = new Set();
function start(mode) {
  const e = { ...env };
  const p = spawn(binary, [mode], {
    env: e,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "",
    error = "";
  p.stdout.on("data", (b) => {
    if (output.length < 65536) output += b;
  });
  p.stderr.on("data", (b) => {
    if (error.length < 65536) error += b;
  });
  p.done = once(p, "close").then(([code, signal]) => ({
    code,
    signal,
    output,
    error,
  }));
  children.add(p);
  return p;
}
async function run(mode) {
  const p = start(mode),
    r = await p.done;
  children.delete(p);
  if (r.code !== 0) {
    await writeFile(join(root, "logs", `${schema}-${mode}.log`), r.error, {
      mode: 0o600,
    });
    throw Error(`Probe ${mode} failed; inspect private qualification log`);
  }
  return r.output;
}
async function stop(p, signal = "SIGINT") {
  const kill = setTimeout(() => {
    if (p.exitCode === null && p.signalCode === null) p.kill("SIGKILL");
  }, 10000);
  kill.unref();
  if (p.exitCode === null && p.signalCode === null) p.kill(signal);
  try {
    await p.done;
  } finally {
    clearTimeout(kill);
  }
  children.delete(p);
}
const evidence = { schema, started: new Date().toISOString(), tests: [] };
const passed = (name) => {
  evidence.tests.push(name);
  console.log("PASS", name);
};
let plan,
  seeded = false,
  samplerA,
  samplerB,
  worker,
  errors,
  originalDatasource;
async function states() {
  const r = await api("/api/prometheus/grafana/api/v1/rules");
  return r.data.groups
    .flatMap((g) => g.rules)
    .filter((r) => r.name.startsWith(`${schema} · `));
}
async function expect(suffix, state) {
  const title = plan.alerts.groups[0].rules.find((r) =>
    r.uid.endsWith(`-${suffix}`),
  ).title;
  await until(async () => {
    const r = (await states()).find((r) => r.name === title);
    return r?.state === state && r.health === "ok";
  }, 90000);
}
async function value(expr, expected) {
  await until(async () => {
    const rows = await query(expr);
    return rows.length === 1 && Number(rows[0].value[1]) === expected;
  }, 45000);
}
try {
  plan = await provision(root, {
    deployment: schema,
    expectedSlots: 2,
    freshSeconds: 20,
    queueSeconds: 3,
    pendingSeconds: 10,
    intervalSeconds: 10,
    errorWindowSeconds: 30,
  });
  await compose(m, [
    "up",
    "-d",
    "--wait",
    "--wait-timeout",
    "120",
    "observability",
  ]);
  await until(async () => (await api("/api/health")).database === "ok", 60000);
  // Compose does not restart a healthy container when only mounted JSON changes.
  // Remove only this reference's earlier qualification rules, never app rules.
  const previous = await api("/api/v1/provisioning/alert-rules");
  plan.alerts.deleteRules = previous
    .filter(
      (r) =>
        /^ow_ops_[a-f0-9]{32}$/.test(r.labels?.deployment ?? "") &&
        r.labels.deployment !== schema,
    )
    .map((r) => ({ orgId: 1, uid: r.uid }));
  await writeFile(
    join(root, "operations-alerts.json"),
    JSON.stringify(plan.alerts, null, 2) + "\n",
    { mode: 0o600 },
  );
  await api("/api/admin/provisioning/alerting/reload", { method: "POST" });
  await api("/api/admin/provisioning/dashboards/reload", { method: "POST" });
  const dashboard = (await api(`/api/dashboards/uid/${plan.dashboard.uid}`))
    .dashboard;
  assert.equal(dashboard.panels.length, 14);
  assert.equal(dashboard.title, plan.dashboard.title);
  assert.equal(
    dashboard.panels[0].targets[0].expr,
    plan.dashboard.panels[0].targets[0].expr,
  );
  const loaded = await api("/api/v1/provisioning/alert-rules");
  assert.equal(
    loaded.filter((r) => r.labels?.deployment === schema).length,
    11,
  );
  passed("11 local-only Grafana rules provisioned");
  seeded = true;
  await run("seed");
  worker = start("worker");
  samplerA = start("sample");
  samplerB = start("sample");
  const q = plan.queries;
  await value(q.active, 1);
  await value(q.queued, 1);
  await value(q.approvals, 1);
  await value(q.capacity, 2);
  passed(
    "real PG snapshot; two samplers do not double-count work; real idle worker advertises 2 slots",
  );
  await expect("queue", "firing");
  await expect("stalled", "firing");
  await expect("unknown", "firing");
  passed(
    "aged queue, stalled active run and recent uncertain transitions fire",
  );
  await run("clear");
  await value(q.active, 0);
  await value(q.approvals, 0);
  await expect("queue", "inactive");
  await expect("stalled", "inactive");
  await expect("unknown", "inactive");
  passed(
    "durable state clear resolves work alerts; waiting approvals were not counted as stalls",
  );
  await stop(worker, "SIGKILL");
  await expect("capacity", "firing");
  worker = start("worker");
  await expect("capacity", "inactive");
  passed("real worker process crash and restart fires/clears capacity alert");
  await run("break-sampler");
  await expect("database", "firing");
  await expect("state", "firing");
  await expect("monitoring", "inactive");
  await stop(samplerA);
  await stop(samplerB);
  samplerA = start("sample");
  samplerB = start("sample");
  await expect("database", "inactive");
  await expect("state", "inactive");
  passed(
    "query timeout from a lock on only the qualification audit table fires observer/stale alerts; restart clears them",
  );
  errors = start("errors");
  await expect("model", "firing");
  await expect("tool", "firing");
  await stop(errors);
  await expect("model", "inactive");
  await expect("tool", "inactive");
  passed(
    "synthetic model/tool error telemetry fires and quiet window clears alerts (no live model calls)",
  );
  await stop(samplerA, "SIGKILL");
  await stop(samplerB, "SIGKILL");
  await expect("monitoring", "firing");
  await expect("state", "firing");
  assert.equal((await query(q.queued)).length, 0);
  samplerA = start("sample");
  await expect("monitoring", "inactive");
  await expect("state", "inactive");
  passed(
    "lost samplers produce missing-telemetry alert and unknown queue, then recover",
  );
  originalDatasource = await api("/api/datasources/uid/prometheus");
  await setDatasourceURL("http://127.0.0.1:1");
  await until(
    async () => (await states()).some((r) => r.health === "error"),
    60000,
  );
  await setDatasourceURL(originalDatasource.url);
  originalDatasource = null;
  await until(
    async () => (await states()).every((r) => r.health === "ok"),
    60000,
  );
  passed(
    "Grafana query failure is an explicit error, not healthy; datasource restoration clears it",
  );
  const labels = await query(
    `{__name__=~"openworker_server_.*|openworker_worker_.*"} and on(job,instance) target_info{openworker_deployment="${schema}"}`,
  );
  assert.ok(labels.length > 0);
  assert.ok(!JSON.stringify(labels).includes("SECRET"));
  assert.ok(
    labels.every(
      (r) =>
        !Object.keys(r.metric).some((k) =>
          /session|action|prompt|argument/.test(k),
        ),
    ),
  );
  passed(
    "Prometheus operational samples carry no fixture content or per-session/action labels",
  );
  evidence.passed = true;
  evidence.dashboard = `http://127.0.0.1:${m.ports.grafana}/d/${plan.dashboard.uid}`;
} finally {
  if (originalDatasource)
    await setDatasourceURL(originalDatasource.url).catch(() => {
      evidence.datasourceRestoreRequired = true;
      console.error("Datasource restore failed; manual recovery required");
    });
  await Promise.all([...children].map((p) => stop(p)));
  if (seeded)
    await run("cleanup").catch(() => {
      evidence.cleanupRequired = schema;
      console.error(
        "Qualification schema cleanup failed; identity saved in evidence",
      );
    });
  // Keep the evidence dashboard; pause this finished experiment's alerts. The
  // next reference start can explicitly regenerate defaults for its deployment.
  if (plan) {
    for (const r of plan.alerts.groups[0].rules) r.isPaused = true;
    await writeFile(
      join(root, "operations-alerts.json"),
      JSON.stringify(plan.alerts, null, 2) + "\n",
      { mode: 0o600 },
    );
    await api("/api/admin/provisioning/alerting/reload", {
      method: "POST",
    }).catch(() => {
      evidence.alertPauseRequired = true;
      console.error(
        "Alert cleanup reload unavailable; saved paused provisioning for next startup",
      );
    });
  }
  evidence.finished = new Date().toISOString();
  if (
    evidence.cleanupRequired ||
    evidence.datasourceRestoreRequired ||
    evidence.alertPauseRequired
  )
    evidence.passed = false;
  await writeFile(
    join(root, "operations-qualification.json"),
    JSON.stringify(evidence, null, 2) + "\n",
    { mode: 0o600 },
  );
}
assert.equal(evidence.passed, true, "Qualification or cleanup incomplete");
console.log(JSON.stringify(evidence, null, 2));
