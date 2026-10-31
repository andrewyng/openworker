// Real local Kubernetes + OpenShell, deterministic model, production service SDK.
// No tool requests are retried after uncertain delivery. Originals are inspected.
import assert from "node:assert/strict";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { createRequire } from "node:module";
import { Harness } from "../../harness/typescript-sdk/dist/index.js";
import { start as startUI } from "../../harness/service-ui/server.mjs";
import { run as command } from "../service/stack.mjs";
import { ownedLocalFixture, cursorTail, noReplay } from "./contract.mjs";
import { collectorRecords, attributes } from "../reference/export-contract.mjs";
const [directory, confirmation, output] = process.argv.slice(2),
  root = resolve(output),
  state = resolve(directory);
const m = JSON.parse(await readFile(join(state, "kubernetes.json")));
const kub = (args, input) =>
  command(
    "kubectl",
    ["--kubeconfig", m.kubeconfig, "--context", m.context, ...args],
    { input, timeout: 240000 },
  );
const kube = (args, input) => kub(["-n", m.namespace, ...args], input);
const json = async (args) => JSON.parse(await kube([...args, "-o", "json"]));
ownedLocalFixture(
  m,
  JSON.parse(await kub(["get", "namespace", m.namespace, "-o", "json"])),
  confirmation,
);
const installedConfig = JSON.parse(
  (await json(["get", "configmap", m.release + "-config"])).data["config.json"],
);
assert.equal(
  installedConfig.scripted,
  true,
  "Actual installed service must use the deterministic model",
);
assert.equal(installedConfig.service.id, m.id);
for (const role of ["api", "worker"]) {
  const d = await json(["get", "deployment", m.release + "-" + role]);
  assert.equal(
    d.spec.template.spec.containers.find((c) => c.name === "harness").image,
    m.imageRef,
    "Qualify the exact declared service image",
  );
}
const save = (name, v) =>
  writeFile(join(root, name + ".json"), JSON.stringify(v, null, 2) + "\n", {
    mode: 0o600,
    flag: "wx",
  });
const report = {
  version: 1,
  passed: false,
  checks: [],
  cleanup: {},
  fixture: {
    serviceId: m.id,
    context: m.context,
    namespace: m.namespace,
    serviceImage: m.imageRef,
    runnerImage: m.runnerImage,
  },
};
await writeFile(
  join(state, "resilience-qualification.json"),
  JSON.stringify({ ...report.fixture, output: root }),
  { mode: 0o600, flag: "wx" },
);
// One destructive qualification per prepared service. Failure requires inspecting
// the saved original operation, not re-running this program against uncertain work.
await save("cluster-intent", report.fixture);
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
async function eventually(f, description, ms = 150000) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    try {
      const v = await f();
      if (v) return v;
    } catch (e) {
      if (e.name === "AssertionError") throw e;
      last = e;
    }
    await pause(300);
  }
  throw Error(
    `Timed out: ${description}${last ? " (inspect scoped logs)" : ""}`,
  );
}
const checks = (name, details = {}) => {
  report.checks.push({ name, ...details });
  console.log("PASS " + name);
};
let h, other, ui, browser;
const children = [],
  sessions = [];
const token = String(await readFile(join(state, "input/admin-token"))).trim();
const credentials = { getToken: async () => token };
const readerImage = m.runnerImage;
const pins = JSON.parse(
  await readFile(
    new URL("../reference/export-qualification-images.json", import.meta.url),
  ),
);
// Test-only central collector with a separate read-only observer of its output.
// All worker OTLP requests use an uncredentialed loopback relay, never HTTPS off.
async function collector() {
  const config = `receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 0.0.0.0:4318\nprocessors:\n  batch:\n    timeout: 100ms\nexporters:\n  file:\n    path: /out/traces.jsonl\n    format: json\n    flush_interval: 100ms\nservice:\n  telemetry:\n    logs:\n      level: warn\n  pipelines:\n    traces:\n      receivers: [otlp]\n      processors: [batch]\n      exporters: [file]\n`;
  const items = [
    {
      apiVersion: "v1",
      kind: "ConfigMap",
      metadata: { name: "resilience-collector" },
      data: { "collector.yaml": config },
    },
    {
      apiVersion: "v1",
      kind: "Service",
      metadata: { name: "resilience-collector" },
      spec: {
        selector: { app: "resilience-collector" },
        ports: [{ port: 4318 }],
      },
    },
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: { name: "resilience-collector" },
      spec: {
        replicas: 1,
        selector: { matchLabels: { app: "resilience-collector" } },
        template: {
          metadata: { labels: { app: "resilience-collector" } },
          spec: {
            automountServiceAccountToken: false,
            securityContext: { fsGroup: 1000 },
            containers: [
              {
                name: "collector",
                image: pins.collector,
                args: ["--config=/config/collector.yaml"],
                securityContext: {
                  runAsUser: 1000,
                  runAsGroup: 1000,
                  allowPrivilegeEscalation: false,
                  readOnlyRootFilesystem: true,
                },
                resources: {
                  requests: { memory: "64Mi", cpu: "20m" },
                  limits: { memory: "128Mi", cpu: "500m" },
                },
                volumeMounts: [
                  { name: "config", mountPath: "/config", readOnly: true },
                  { name: "out", mountPath: "/out" },
                ],
              },
              {
                name: "reader",
                image: readerImage,
                command: ["sleep", "infinity"],
                securityContext: {
                  runAsUser: 1000,
                  runAsGroup: 1000,
                  allowPrivilegeEscalation: false,
                  readOnlyRootFilesystem: true,
                },
                resources: {
                  requests: { memory: "8Mi", cpu: "1m" },
                  limits: { memory: "32Mi", cpu: "100m" },
                },
                volumeMounts: [
                  { name: "out", mountPath: "/out", readOnly: true },
                ],
              },
            ],
            volumes: [
              { name: "config", configMap: { name: "resilience-collector" } },
              { name: "out", emptyDir: { sizeLimit: "32Mi" } },
            ],
          },
        },
      },
    },
  ];
  await kube(
    ["apply", "-f", "-"],
    JSON.stringify({ apiVersion: "v1", kind: "List", items }),
  );
  await kube([
    "rollout",
    "status",
    "deployment/resilience-collector",
    "--timeout=120s",
  ]);
  const relay = `const h=require('http');h.createServer((q,s)=>{const r=h.request({host:'resilience-collector',port:4318,path:q.url,method:q.method,headers:q.headers},v=>{s.writeHead(v.statusCode,v.headers);v.pipe(s)});r.on('error',()=>{if(!s.headersSent)s.writeHead(503);s.end()});q.pipe(r)}).listen(4318,'127.0.0.1')`;
  for (const role of ["worker", "api"]) {
    const patch = {
      spec: {
        template: {
          spec: {
            containers: [
              {
                name: "resilience-relay",
                image: readerImage,
                command: ["node", "-e", relay],
                securityContext: {
                  runAsUser: 1000,
                  runAsGroup: 1000,
                  allowPrivilegeEscalation: false,
                  readOnlyRootFilesystem: true,
                },
                resources: {
                  requests: { memory: "24Mi", cpu: "10m" },
                  limits: { memory: "64Mi", cpu: "200m" },
                },
              },
            ],
          },
        },
      },
    };
    await kube([
      "patch",
      "deployment",
      m.release + "-" + role,
      "--type=strategic",
      "-p",
      JSON.stringify(patch),
    ]);
  }
  await kube(["scale", "deployment/" + m.release + "-api", "--replicas=2"]);
  await kube([
    "rollout",
    "status",
    "deployment/" + m.release + "-worker",
    "--timeout=180s",
  ]);
  await kube([
    "rollout",
    "status",
    "deployment/" + m.release + "-api",
    "--timeout=180s",
  ]);
}
async function forward(pod, port) {
  const p = spawn(
    "kubectl",
    [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "-n",
      m.namespace,
      "port-forward",
      "pod/" + pod,
      `${port}:8443`,
      "--address",
      "127.0.0.1",
    ],
    { stdio: ["ignore", "ignore", "ignore"] },
  );
  children.push(p);
  const url = `https://localhost:${port}`;
  return eventually(
    async () => {
      try {
        return await Harness.connect({ url, namespace: "local", credentials });
      } catch {
        return false;
      }
    },
    "authenticated API forward",
    30000,
  );
}
const events = async (s, after = 0) =>
  (await h.json("GET", `/v1/sessions/${s.id}/events/page?after=${after}`))
    .events;
async function terminal(run, expected = "completed") {
  return eventually(
    async () => {
      const r = await run.snapshot();
      if (r.status === "running") return false;
      assert.equal(r.status, expected);
      return r;
    },
    "terminal run " + expected,
    180000,
  );
}
function plan(steps) {
  return (
    "TEST_RESOURCE_GRANTS " +
    JSON.stringify(steps.map(([name, args]) => ({ name, arguments: args })))
  );
}
async function send(s, label, steps, expected = "completed") {
  await save(label + "-intent", {
    sessionId: s.id,
    text: typeof steps === "string" ? steps : plan(steps),
  });
  const r = await s.send(typeof steps === "string" ? steps : plan(steps), {
    idempotencyKey: label,
  });
  await save(label + "-run", { runId: r.id, submissionId: r.submissionId });
  await terminal(r, expected);
  const e = (await events(s)).filter((e) => e.turn_id === r.id);
  await save(label + "-events", e);
  return { run: r, events: e };
}
function result(e, tool) {
  const r = e
    .filter((e) => e.type === "tool_finished" && e.data.name === tool)
    .at(-1);
  assert(r, `Expected result for ${tool}`);
  const envelope = r.data.result;
  assert(
    envelope?.isError !== true && envelope?.denied !== true,
    `Tool ${tool} failed`,
  );
  const data = envelope?.content
    ? JSON.parse(envelope.content[0].text)
    : envelope;
  assert(
    data && data.isError !== true && data.executed !== false,
    `Tool ${tool} was not executed`,
  );
  return data;
}
async function killWorker() {
  const workers = (
    await json(["get", "pods", "-l", "app=" + m.release + "-worker"])
  ).items.filter(
    (p) => p.status.phase === "Running" && !p.metadata.deletionTimestamp,
  );
  assert.equal(
    workers.length,
    1,
    "Dedicated single-worker fault fixture required",
  );
  const p = workers[0];
  await save("fault-" + report.checks.length, {
    pod: p.metadata.name,
    uid: p.metadata.uid,
    action: "zero-grace deletion of owned worker pod",
  });
  // A zero-grace deletion forces an abrupt container stop from the kubelet;
  // do not rely on an unprivileged process being able to kill namespace PID 1.
  // Issue once, then reconcile pod identity and readiness without repeating it.
  await kube([
    "delete",
    "pod",
    p.metadata.name,
    "--grace-period=0",
    "--force",
    "--wait=false",
  ]);
  await eventually(async () => {
    const pods = (
      await json(["get", "pods", "-l", "app=" + m.release + "-worker"])
    ).items;
    return (
      !pods.some((n) => n.metadata.uid === p.metadata.uid) &&
      pods.some(
        (n) =>
          !n.metadata.deletionTimestamp &&
          n.status.containerStatuses?.every((c) => c.ready),
      )
    );
  }, "worker pod replacement");
}
async function recording(s, run, required) {
  return eventually(
    async () => {
      const t = await h.json(
        "GET",
        `/v1/sessions/${s.id}/observability/${run.id}/timeline?limit=200`,
      );
      return required.every((op) =>
        t.page.steps.some((x) => x.step.operation === op),
      )
        ? t
        : false;
    },
    "recorded operations " + required.join(","),
    20000,
  );
}
async function collectWire() {
  // The shared installer helper intentionally caps stdout at 2 MB. Collector
  // output can exceed that during fault/recovery queries, so read the actual
  // bounded 32 MiB volume explicitly instead of silently parsing a prefix.
  const { stdout: raw } = await promisify(execFile)(
    "kubectl",
    [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "-n",
      m.namespace,
      "exec",
      "deployment/resilience-collector",
      "-c",
      "reader",
      "--",
      "cat",
      "/out/traces.jsonl",
    ],
    { timeout: 30000, maxBuffer: 40 * 1024 * 1024 },
  );
  return collectorRecords(
    raw.trim().split("\n").filter(Boolean).map(JSON.parse),
  );
}
try {
  await collector();
  const apis = (
    await json(["get", "pods", "-l", "app=" + m.release + "-api"])
  ).items.filter(
    (p) => p.status.phase === "Running" && !p.metadata.deletionTimestamp,
  );
  assert.equal(apis.length, 2);
  h = await forward(apis[0].metadata.name, m.port);
  other = await forward(apis[1].metadata.name, m.port + 1);
  assert.equal(h.capabilities.serviceId, m.id);
  assert.equal(other.capabilities.serviceId, m.id);
  const baseline = JSON.parse(await kub(["get", "pods", "-A", "-o", "json"]))
    .items.filter(
      (p) =>
        p.metadata.namespace !== m.namespace &&
        (p.metadata.name.includes("--owr-") ||
          p.metadata.name.startsWith("os-supervisor-")),
    )
    .map((p) => p.metadata.uid);
  await save("baseline-environment-pods", baseline);
  await h.registerRuntimeProfile(
    {
      id: "resilience@1",
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "shared",
      approvalMode: "bypass",
      autoSkipQuestions: true,
    },
    { idempotencyKey: "resilience-profile" },
  );
  await h.activateRuntimeProfile("resilience@1", {
    alias: "resilience-runtime",
    idempotencyKey: "resilience-profile-activate",
  });
  await h.deployAgent(
    {
      id: "resilience@1",
      instructions:
        "Run the deterministic qualification plan. Inspect results; never replay uncertain effects.",
      tools: [
        "read_file",
        "write_file",
        "run_shell",
        "process_start",
        "process_status",
        "process_logs",
        "process_stop",
      ],
      runtimeProfiles: ["resilience@1"],
      defaultRuntimeProfile: "resilience@1",
    },
    { idempotencyKey: "resilience-release" },
  );
  await h.activateAgent("resilience@1", {
    alias: "resilience",
    idempotencyKey: "resilience-activate",
  });
  const agent = await h.agent("resilience");
  await save("coder-admission-intent", {
    idempotencyKey: "session-coder",
    agent: "resilience",
  });
  const a = await agent.newSession({
    project: "resilience",
    name: "Resilience coding",
    idempotencyKey: "session-coder",
    waitTimeoutMs: 180000,
  });
  sessions.push(a);
  await save("coder", a.reference());
  const b = await agent.newSession({
    environmentId: a.environmentId,
    project: "resilience",
    name: "Resilience verifier",
    idempotencyKey: "session-verifier",
    waitTimeoutMs: 180000,
  });
  sessions.push(b);
  assert.equal(b.environmentId, a.environmentId);
  await save("verifier", b.reference());
  const env = await h.json("GET", "/v1/environments/" + a.environmentId);
  await save("environment", env);
  const remote = env.environment.binding.remote_name;
  assert(remote.startsWith("owr-"));
  const outside = JSON.parse(
    await kub(["get", "pods", "-A", "-o", "json"]),
  ).items.filter((p) => p.metadata.name.endsWith("--" + remote));
  assert.equal(outside.length, 1);
  const sandboxPod = outside[0];
  const allEnvironmentPods = JSON.parse(
    await kub(["get", "pods", "-A", "-o", "json"]),
  )
    .items.filter(
      (p) =>
        p.metadata.uid === sandboxPod.metadata.uid ||
        p.metadata.name ===
          "os-supervisor-" + env.environment.binding.remote_id,
    )
    .map((p) => p.metadata.uid);
  assert.equal(
    allEnvironmentPods.length,
    2,
    "Observe both the original sandbox and its supervisor before testing deletion",
  );
  await save("original-environment-pods", allEnvironmentPods);
  // Genuine independent sessions, same environment. Deliberately cooperative
  // directories/ports; sharing is not per-session OS isolation.
  const start = async (s, label, port) =>
    send(s, "start-" + label, [
      [
        "run_shell",
        {
          command: `mkdir -p ${label}; printf ${label} > ${label}/index.txt`,
          yield_ms: 10000,
        },
      ],
      [
        "process_start",
        {
          command: `node -e 'const fs=require("fs"),h=require("http");h.createServer((q,s)=>s.end(fs.readFileSync("${label}/index.txt","utf8"))).listen(${port},"127.0.0.1")'`,
          timeout_seconds: 900,
        },
      ],
    ]);
  const [startedA, startedB] = await Promise.all([
    start(a, "coder", 4000),
    start(b, "verifier", 4001),
  ]);
  const pa = result(startedA.events, "process_start").process_id,
    pb = result(startedB.events, "process_start").process_id;
  assert(pa && pb && pa !== pb);
  await save("processes", { coder: pa, verifier: pb });
  await recording(a, startedA.run, ["process.started"]);
  await recording(b, startedB.run, ["process.started"]);
  const health = () =>
    send(b, "health-" + report.checks.length, [
      [
        "run_shell",
        {
          command:
            'test "$(curl -fsS --max-time 3 http://127.0.0.1:4000)" = coder && test "$(curl -fsS --max-time 3 http://127.0.0.1:4001)" = verifier && printf both-healthy',
          yield_ms: 10000,
        },
      ],
    ]);
  assert.equal(result((await health()).events, "run_shell").exit_code, 0);
  checks(
    "two sessions share environment with separate files, handles and ports",
  );
  const stream = await a.send("TEST_RESILIENCE_STREAM", {
    idempotencyKey: "stream-fault",
    limits: { maxModelCalls: 7, maxOutputTokens: 20000, summaryTokens: 512 },
  });
  await save("stream-run", {
    runId: stream.id,
    submissionId: stream.submissionId,
  });
  await eventually(
    async () =>
      (await events(a)).some(
        (e) => e.turn_id === stream.id && e.type === "assistant_delta",
      ),
    "partial stream barrier",
  );
  const before = await events(a),
    snapshot = await a.snapshot();
  const budget = snapshot.turn.budget;
  await recording(a, stream, ["model.requested"]);
  await killWorker();
  const rebound = await other.resume(a.reference());
  await terminal(await rebound.resumeRun(stream.submissionId), "interrupted");
  assert.deepEqual((await rebound.snapshot()).turn.budget, budget);
  const tail = (
    await other.json(
      "GET",
      `/v1/sessions/${a.id}/events/page?after=${before.at(-1).event_id}`,
    )
  ).events;
  cursorTail(before, tail);
  assert.deepEqual(
    [...before, ...tail].map((e) => e.event_id),
    (await events(a)).map((e) => e.event_id),
    "Reconnect must recover the complete durable tail",
  );
  assert(!tail.some((e) => e.type === "tool_started"));
  await save("stream-recovered", { snapshot: await rebound.snapshot(), tail });
  assert.equal(result((await health()).events, "run_shell").exit_code, 0);
  checks(
    "worker killed during streaming: budgets and cursor survive, no partial tool accepted",
  );
  const effect = await a.send(
    plan([
      [
        "run_shell",
        {
          command:
            "printf once >> effects.txt; sleep 8; printf finished > effect-finished",
          yield_ms: 10000,
          timeout_seconds: 30,
        },
      ],
    ]),
    { idempotencyKey: "tool-fault" },
  );
  await save("effect-run", {
    runId: effect.id,
    submissionId: effect.submissionId,
  });
  await eventually(
    async () =>
      (await events(a)).some(
        (e) => e.turn_id === effect.id && e.type === "tool_started",
      ),
    "tool admission",
  );
  // Read-only observation of a real effect in the exact original sandbox. This
  // cannot launch/replay the side effect; wait until it actually happened.
  await eventually(async () => {
    try {
      return (
        (await kub([
          "-n",
          sandboxPod.metadata.namespace,
          "exec",
          sandboxPod.metadata.name,
          "--",
          "cat",
          "/sandbox/effects.txt",
        ])) === "once"
      );
    } catch {
      return false;
    }
  }, "durable effect before result");
  await killWorker();
  await terminal(await rebound.resumeRun(effect.submissionId), "interrupted");
  const recovered = await rebound.snapshot();
  assert.equal(
    recovered.turn.queue.length,
    0,
    "Started uncertain action must not remain replayable",
  );
  assert.match(a.id, /^s_[a-f0-9]{32}$/);
  const unknownCount = await kube([
    "exec",
    "deployment/postgres",
    "--",
    "psql",
    "-U",
    "openworker",
    "-d",
    "openworker",
    "-Atc",
    `SELECT count(*) FROM ow_service.history WHERE session_id='${a.id}' AND data->>'type'='function_call_output' AND data->>'output' LIKE '%tool_outcome_unknown%'`,
  ]);
  assert.equal(unknownCount.trim(), "1");
  const effectEvents = (await events(a)).filter((e) => e.turn_id === effect.id);
  const cid = effectEvents.find((e) => e.type === "tool_started").data.call_id;
  noReplay(effectEvents, cid);
  await save("unknown-effect", { snapshot: recovered, events: effectEvents });
  const inspected = await send(b, "inspect-effect", [
    [
      "run_shell",
      {
        command:
          'test "$(cat effects.txt)" = once && test -f effect-finished && printf inspected-once',
        yield_ms: 10000,
      },
    ],
  ]);
  assert.equal(result(inspected.events, "run_shell").exit_code, 0);
  await recording(a, effect, ["session.recovery", "tool.unknown_outcome"]);
  checks(
    "worker killed after real tool effect: unknown outcome persisted once, explicit read-back, no replay",
  );
  const waiting = await a.send("TEST_RESILIENCE_STREAM", {
    idempotencyKey: "cancel-stream",
  });
  await eventually(
    async () =>
      (await events(a)).some(
        (e) => e.turn_id === waiting.id && e.type === "assistant_delta",
      ),
    "cancellation barrier",
  );
  await rebound.control(
    waiting.id,
    { kind: "cancel" },
    { idempotencyKey: "cancel-via-other-api" },
  );
  await terminal(waiting, "cancelled");
  assert.equal(result((await health()).events, "run_shell").exit_code, 0);
  await recording(a, waiting, ["turn.cancellation"]);
  checks(
    "cross-API cancellation stops turn and preserves environment-owned servers",
  );
  const exp = await send(a, "expire-process", [
    [
      "process_start",
      {
        command: "(sleep 3; echo orphan > expiry-orphan.txt) & wait",
        timeout_seconds: 1,
      },
    ],
  ]);
  const px = result(exp.events, "process_start").process_id;
  await pause(3300);
  const expiry = await send(a, "observe-expiry", [
    ["process_status", { process_id: px }],
    ["run_shell", { command: "test ! -e expiry-orphan.txt", yield_ms: 10000 }],
  ]);
  assert.equal(result(expiry.events, "process_status").status, "timed_out");
  assert.equal(result(expiry.events, "run_shell").exit_code, 0);
  const stop = await send(a, "stop-coder", [
    ["process_stop", { process_id: pa }],
    ["process_status", { process_id: pb }],
    [
      "run_shell",
      {
        command:
          '! curl -fsS --max-time 2 http://127.0.0.1:4000 && test "$(curl -fsS --max-time 2 http://127.0.0.1:4001)" = verifier',
        yield_ms: 10000,
      },
    ],
  ]);
  assert.equal(result(stop.events, "process_stop").status, "stopped");
  await recording(a, stop.run, ["process.stopped"]);
  assert.equal(result(stop.events, "process_status").status, "running");
  assert.equal(result(stop.events, "run_shell").exit_code, 0);
  checks(
    "deadline kills descendant without status polling; stopping coder leaves verifier healthy",
  );
  const budgetRun = await a.send(
    plan([
      [
        "write_file",
        { path: "must-not-exist", content: "PRIVATE_RESILIENCE_CONTENT" },
      ],
    ]),
    {
      idempotencyKey: "budget-stop",
      limits: { maxModelCalls: 1, maxOutputTokens: 1024, summaryTokens: 256 },
    },
  );
  await terminal(budgetRun);
  const proof = await send(b, "budget-proof", [
    ["run_shell", { command: "test ! -e must-not-exist", yield_ms: 10000 }],
  ]);
  assert.equal(result(proof.events, "run_shell").exit_code, 0);
  await recording(a, budgetRun, ["turn.budget_exceeded"]);
  checks("budget exhaustion refuses queued file effect and records the stop");
  const normal = await send(b, "observability-proof", [
    ["process_status", { process_id: pb }],
    ["process_logs", { process_id: pb }],
  ]);
  const recorded = await recording(b, normal.run, [
    "model.attempt",
    "tool.execute",
  ]);
  const base = `/v1/sessions/${b.id}/observability/${normal.run.id}`;
  const summary = (
    await h.json("GET", base + "/summary?through=" + recorded.page.through)
  ).summary;
  const rows = recorded.page.steps.map((x) => x.step);
  await save("recording-before-export", { summary, timeline: recorded });
  assert.equal(
    summary.models.reduce((n, g) => n + g.calls, 0),
    rows.filter((r) => r.operation === "model.attempt").length,
  );
  assert.equal(
    summary.tools.reduce((n, g) => n + g.calls, 0),
    rows.filter((r) => r.operation === "tool.execute").length,
  );
  // This scripted model supplies no usage. Missing counters must stay unknown.
  assert.equal(summary.usage.output.total, null);
  assert(summary.usage.output.unknown > 0);
  const wire = await eventually(
    async () => {
      const w = await collectWire();
      return rows.every((r) =>
        w.spans.some((s) => s.spanId === r.span_id && s.traceId === r.trace_id),
      )
        ? w
        : false;
    },
    "same run exported and recorded",
    30000,
  );
  for (const r of rows) {
    const matches = wire.spans.filter((s) => s.spanId === r.span_id);
    assert.equal(matches.length, 1);
    assert.equal(
      attributes(matches[0].attributes)["openworker.operation"],
      r.operation,
    );
  }
  const serialized = JSON.stringify(wire);
  assert(!serialized.includes(token));
  assert(!serialized.includes("PRIVATE_RESILIENCE_CONTENT"));
  assert(!serialized.includes("TEST_RESOURCE_GRANTS"));
  await save("recording-export-proof", {
    summary,
    timeline: recorded,
    exportedSpans: wire.spans.filter((s) =>
      rows.some((r) => r.span_id === s.spanId),
    ),
  });
  checks(
    "management summaries agree with timeline and actual OTLP collector by span/trace; content redacted",
  );
  const require = createRequire(import.meta.url);
  assert(
    process.env.OW_PLAYWRIGHT_MODULE,
    "Set OW_PLAYWRIGHT_MODULE to the installed playwright package",
  );
  const { chromium } = require(process.env.OW_PLAYWRIGHT_MODULE);
  ui = await startUI({
    port: 0,
    serviceUrl: m.url,
    serviceId: m.id,
    namespace: "local",
  });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(ui.url + "/#observability");
  await page.locator("#token").fill(token);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.locator("#obs-run-search").fill(normal.run.id);
  await page
    .locator(".obs-run-option")
    .filter({ hasText: normal.run.id })
    .first()
    .click();
  await page.locator(".obs-stats").waitFor();
  await page.screenshot({
    path: join(root, "management-usage.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await page.locator(".obs-lanes").waitFor();
  await page.screenshot({
    path: join(root, "management-timeline.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  checks(
    "browser finds exact run and renders grouped usage and timeline without page errors",
  );
  await browser.close();
  browser = null;
  await ui.close();
  ui = null;
  for (const s of sessions)
    await h.json("POST", `/v1/sessions/${s.id}/close`, {}, "close-" + s.id);
  await h.json(
    "POST",
    `/v1/environments/${a.environmentId}/delete`,
    {},
    "delete-original-environment",
  );
  await eventually(
    async () =>
      (await h.json("GET", "/v1/environments/" + a.environmentId)).environment
        .phase === "deleted",
    "original environment deletion",
  );
  await eventually(
    async () =>
      !JSON.parse(await kub(["get", "pods", "-A", "-o", "json"])).items.some(
        (p) => allEnvironmentPods.includes(p.metadata.uid),
      ),
    "original sandbox and supervisor pod disappearance",
  );
  const after = JSON.parse(
    await kub(["get", "pods", "-A", "-o", "json"]),
  ).items;
  assert(
    baseline.every((uid) => after.some((p) => p.metadata.uid === uid)),
    "Unrelated sandbox pods must remain",
  );
  report.cleanup.originalEnvironmentDeleted = true;
  report.cleanup.unrelatedSandboxesUnchanged = true;
  checks(
    "environment deletion removes remaining server and original pod; other sandboxes retained",
  );
  report.passed = true;
} catch (error) {
  report.failure = {
    type: error.name,
    code: error.code ?? null,
    operationId: error.operationId ?? null,
  };
  if (error.operationId && h) {
    const original = await h.operation(error.operationId).catch(() => null);
    if (original) await save("original-uncertain-operation", original);
  }
  throw error;
} finally {
  await browser?.close();
  await ui?.close();
  h?.disconnect();
  other?.disconnect();
  for (const p of children) p.kill("SIGTERM");
  report.cleanup.serviceNamespaceRetained = true; // Entry command destroys it only after successful environment cleanup.
  await save("cluster-report", report);
}
