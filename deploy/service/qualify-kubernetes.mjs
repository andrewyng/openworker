// Local M4 acceptance through the shipped CLI; real Kubernetes/OpenShell effects.
import assert from "node:assert/strict";
import https from "node:https";
import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { run } from "./stack.mjs";
const [directory, binary] = process.argv.slice(2),
  root = resolve(directory),
  cliBin = resolve(binary);
const m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
let replacementForward;
process.on("exit", () => replacementForward?.kill("SIGTERM"));
const config = join(root, "cli.json"),
  prefix = Date.now().toString(36);
const releaseId = `builder@${prefix}`;
const kube = (args) =>
  run("kubectl", [
    "--kubeconfig",
    m.kubeconfig,
    "--context",
    m.context,
    "-n",
    m.namespace,
    ...args,
  ]);
async function file(name, value) {
  const p = join(root, name);
  await writeFile(
    p,
    typeof value === "string" ? value : JSON.stringify(value),
    { mode: 0o600 },
  );
  return p;
}
async function cli(args, { fail = false, token } = {}) {
  const a = [...args],
    at = a.indexOf("--idempotency-key");
  if (at >= 0) a[at + 1] = prefix + "-" + a[at + 1];
  if (token) a.unshift("--token-file", token);
  const r = await new Promise((ok, no) => {
    const p = spawn(cliBin, ["--config", config, ...a]);
    let out = "",
      err = "";
    p.stdout.on("data", (b) => (out += b));
    p.stderr.on("data", (b) => (err += b));
    p.on("error", no);
    const t = setTimeout(() => p.kill("SIGTERM"), 180000);
    p.on("close", (code) => {
      clearTimeout(t);
      ok({ code, out, err });
    });
  });
  if (fail) {
    assert.notEqual(r.code, 0);
    return r;
  }
  assert.equal(r.code, 0, `ows ${args[0]}: ${r.err}`);
  return r.out.trim().split("\n").filter(Boolean).map(JSON.parse).at(-1);
}
async function httpStatus(path, tokenFile, body) {
  const ca = await readFile(join(root, "input/api-ca.crt"));
  const token = (await readFile(tokenFile, "utf8")).trim();
  return new Promise((ok, no) => {
    const q = https.request(
      m.url + path,
      {
        method: body ? "POST" : "GET",
        ca,
        headers: {
          Authorization: `Bearer ${token}`,
          "x-openworker-namespace": "local",
          "x-openworker-service-id": m.id,
          "content-type": "application/json",
          "idempotency-key": prefix + "-boundary",
        },
      },
      (r) => {
        r.resume();
        r.on("end", () => ok(r.statusCode));
      },
    );
    q.on("error", no);
    q.setTimeout(5000, () => q.destroy(Error("HTTP test timeout")));
    q.end(body ? JSON.stringify(body) : undefined);
  });
}
async function eventually(fn, seconds = 150) {
  const until = Date.now() + seconds * 1000;
  let last;
  while (Date.now() < until) {
    try {
      const result = await fn();
      if (result) return result;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw last ?? Error("Timed out waiting for observed state");
}
async function pool() {
  return (await cli(["pool", "list"])).pools.find(
    (p) => p.binding.id === "default",
  );
}
async function scale(replicas, key) {
  const p = await pool();
  return cli([
    "pool",
    "scale",
    "default",
    "--replicas",
    String(replicas),
    "--expected-revision",
    String(p.revision),
    "--idempotency-key",
    key,
  ]);
}
async function applied(replicas) {
  return eventually(async () => {
    const p = await pool();
    return (
      p?.observed.status === "applied" && p.observed.replicas === replicas && p
    );
  });
}
async function issue(name, role, seconds = 3600) {
  const token = join(root, `${prefix}-${name}.token`);
  const spec = await file(`${name}.spec.json`, {
    principal: name,
    role,
    scope: { kind: role === "operator" ? "owned" : "all" },
    expires_at: Math.floor(Date.now() / 1000) + seconds,
  });
  const receipt = await cli([
    "credentials",
    "issue",
    "--spec",
    spec,
    "--out",
    token,
    "--idempotency-key",
    `issue-${name}`,
  ]);
  return { token, receipt };
}
const originalIdentity = await eventually(() => cli(["whoami"]));
const originalConfig = JSON.parse(await readFile(config, "utf8"));
const originalToken =
  originalConfig.contexts[originalConfig.selected].tokenFile;
const admin = await issue("service-admin", "service_administrator");
await cli(["whoami"], { token: admin.token });
originalConfig.contexts[originalConfig.selected].tokenFile = admin.token;
await writeFile(config, JSON.stringify(originalConfig, null, 2), {
  mode: 0o600,
});
await cli(["credentials", "revoke", originalIdentity.credential_id]);
await cli(["whoami"], { token: originalToken, fail: true });
await kube([
  "delete",
  "secret",
  "ow-service-bootstrap",
  "--ignore-not-found=true",
]);
await eventually(() => pool());
assert.equal(await httpStatus("/credentials", admin.token, {}), 404);
assert.equal(await httpStatus("/sessions", admin.token, {}), 404);
assert.equal(await httpStatus("/v1/agents/installed-tools", admin.token), 404);
assert.equal(
  await httpStatus("/v1/sessions", admin.token, { agent: "installed-tools" }),
  403,
); // service-admin cannot submit at all

const publisher = await issue("publisher", "publisher"),
  releaser = await issue("releaser", "release_administrator"),
  runner = await issue("runner", "operator");
assert.equal(
  await httpStatus("/v1/sessions", runner.token, { agent: "installed-tools" }),
  404,
);
const expiring = await issue("expiry-check", "observer", 4);
await cli(["whoami"], { token: expiring.token });
await eventually(async () => {
  const r = await cli(["whoami"], { token: expiring.token, fail: true });
  return r.code !== 0;
}, 10);
const profile = await file("m4-profile.json", {
  id: "local@1",
  provider: "openshell",
  cpu: 1,
  memoryMB: 512,
  sharing: "shared",
  approvalMode: "bypass",
  allowedPrincipals: ["runner"],
});
const agent = await file("m4-agent.json", {
  id: releaseId,
  instructions: "Complete the requested task using the provided tools.",
  tools: ["read_file", "write_file", "run_shell"],
  runtimeProfiles: ["local@1"],
  defaultRuntimeProfile: "local@1",
  allowedPrincipals: ["runner"],
});
await cli([
  "profile",
  "deploy",
  "--file",
  profile,
  "--idempotency-key",
  "profile",
]);
await cli([
  "profile",
  "activate",
  "local@1",
  "--alias",
  "default",
  "--idempotency-key",
  "profile-active",
]);
await cli(
  ["agent", "deploy", "--file", agent, "--idempotency-key", "release"],
  { token: publisher.token },
);
assert.equal(
  (await cli(["agent", "get", releaseId], { token: publisher.token }))
    .activated,
  false,
);
await cli(
  [
    "agent",
    "activate",
    releaseId,
    "--alias",
    "builder",
    "--idempotency-key",
    "denied",
  ],
  { token: publisher.token, fail: true },
);
await cli(
  [
    "agent",
    "activate",
    releaseId,
    "--alias",
    "builder",
    "--idempotency-key",
    "active",
  ],
  { token: releaser.token },
);
let c = await cli(["configuration", "get"]);
c.configuration.compaction.context_window = 64000;
await cli([
  "configuration",
  "set",
  "--file",
  await file("settings.json", c.configuration),
  "--expected-revision",
  String(c.revision),
  "--idempotency-key",
  "settings",
]);
await scale(0, "initial-zero");
await applied(0);
const task = await file(
  "m4-task.txt",
  'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"m4-proof.txt","content":"M4 Kubernetes proof"}}]',
);
const submitted = await cli(
  [
    "submit",
    "--agent",
    "builder",
    "--project",
    "m4",
    "--message-file",
    task,
    "--idempotency-key",
    "queued",
    "--wait",
  ],
  { token: runner.token },
);
const op = submitted.operation;
await cli(
  [
    "cancel",
    "--session",
    op.sessionId,
    "--run",
    op.runId,
    "--idempotency-key",
    "admin-cannot-cancel",
  ],
  { fail: true },
);
let snapshot = await cli(["status", "--session", op.sessionId], {
  token: runner.token,
});
assert.equal(
  snapshot.service.configuration.configuration.compaction.context_window,
  64000,
);
assert.ok(!snapshot.turn);
assert.match(op.sessionId, /^s_[a-f0-9]+$/);
const storedCompaction = await kube([
  "exec",
  "deployment/postgres",
  "--",
  "psql",
  "-U",
  "openworker",
  "-d",
  "openworker",
  "-Atc",
  `SELECT data->'compaction_config'->>'context_window' FROM ow_service.sessions WHERE id='${op.sessionId}'`,
]);
assert.equal(storedCompaction.trim(), "64000");
c = await cli(["configuration", "get"]);
c.configuration.admissionsPaused = true;
c.configuration.compaction.context_window = 32000;
await cli([
  "configuration",
  "set",
  "--file",
  await file("settings.json", c.configuration),
  "--expected-revision",
  String(c.revision),
  "--idempotency-key",
  "pause",
]);
await cli(
  [
    "submit",
    "--agent",
    "builder",
    "--project",
    "m4",
    "--message-file",
    task,
    "--idempotency-key",
    "denied-paused",
  ],
  { token: runner.token, fail: true },
);
assert.equal(
  (await cli(["status", "--session", op.sessionId], { token: runner.token }))
    .service.configuration.configuration.compaction.context_window,
  64000,
);
await scale(1, "resume-workers");
await applied(1);
assert.equal(
  (
    await cli(["watch", "--session", op.sessionId, "--run", op.runId], {
      token: runner.token,
    })
  ).result.status,
  "completed",
);
c = await cli(["configuration", "get"]);
c.configuration.admissionsPaused = false;
await cli([
  "configuration",
  "set",
  "--file",
  await file("settings.json", c.configuration),
  "--expected-revision",
  String(c.revision),
  "--idempotency-key",
  "unpause",
]);
const readTask = await file(
  "read-proof.txt",
  'TEST_RESOURCE_GRANTS [{"name":"read_file","arguments":{"path":"m4-proof.txt"}}]',
);
const readRun = await cli(
  [
    "send",
    "--session",
    op.sessionId,
    "--message-file",
    readTask,
    "--idempotency-key",
    "read-proof",
  ],
  { token: runner.token },
);
const replay = await run(cliBin, [
  "--config",
  config,
  "--token-file",
  runner.token,
  "watch",
  "--session",
  op.sessionId,
  "--run",
  readRun.runId,
]);
assert.ok(replay.includes("M4 Kubernetes proof"));
assert.ok(replay.includes('"status":"completed"'));
const slow = await file(
  "slow.txt",
  'TEST_RESOURCE_GRANTS [{"name":"run_shell","arguments":{"command":"printf before > drain-proof.txt; sleep 8; printf after >> drain-proof.txt; cat drain-proof.txt"}}]',
);
const next = await cli(
  [
    "send",
    "--session",
    op.sessionId,
    "--message-file",
    slow,
    "--idempotency-key",
    "slow",
  ],
  { token: runner.token },
);
const watcher = spawn(cliBin, [
  "--config",
  config,
  "--token-file",
  runner.token,
  "watch",
  "--session",
  op.sessionId,
  "--run",
  next.runId,
]);
let bytes = "";
let started;
const start = new Promise((r) => (started = r));
watcher.stdout.on("data", (b) => {
  bytes += b;
  if (bytes.includes('"type":"tool_started"')) started();
});
const done = new Promise((ok, no) => {
  watcher.on("error", no);
  watcher.on("close", (code) =>
    code === 0 ? ok() : no(Error("Watcher failed")),
  );
});
await Promise.race([
  start,
  new Promise((_, reject) =>
    setTimeout(() => reject(Error("Tool did not start")), 30000),
  ),
]);
await scale(0, "drain-active");
await done;
assert.ok(bytes.includes("beforeafter"));
assert.ok(bytes.includes('"status":"completed"'));
await applied(0);
await scale(2, "two-workers");
await applied(2);
await run(m.helm, [
  "upgrade",
  m.release,
  resolve("deploy/service/chart"),
  "--kubeconfig",
  m.kubeconfig,
  "--kube-context",
  m.context,
  "-n",
  m.namespace,
  "-f",
  join(root, "values.json"),
  "--wait",
  "--timeout",
  "120s",
]);
assert.equal(
  JSON.parse(await kube(["get", "deployment/ows-worker", "-o", "json"])).spec
    .replicas,
  2,
);
await kube(["scale", "deployment/ows-platform", "--replicas=0"]);
await eventually(async () => (await pool()).observed.status === "unknown", 40);
await kube(["scale", "deployment/ows-platform", "--replicas=1"]);
await applied(2);
assert.equal((await pool()).desiredReplicas, 2);
// Public service identity survives an API rollout. Controllers retain original environment bindings.
const oldApiPods = JSON.parse(
  await kube(["get", "pods", "-l", `app=${m.release}-api`, "-o", "json"]),
).items.map((p) => p.metadata.name);
await kube(["rollout", "restart", "deployment/ows-api"]);
await kube(["rollout", "status", "deployment/ows-api", "--timeout=90s"]);
for (const pod of oldApiPods)
  await kube(["wait", "--for=delete", `pod/${pod}`, "--timeout=90s"]);
await eventually(async () => {
  if (
    !replacementForward ||
    replacementForward.exitCode !== null ||
    replacementForward.signalCode !== null
  ) {
    replacementForward = spawn(
      "kubectl",
      [
        "--kubeconfig",
        m.kubeconfig,
        "--context",
        m.context,
        "-n",
        m.namespace,
        "port-forward",
        "service/ows-api",
        `${m.port}:443`,
        "--address",
        "127.0.0.1",
      ],
      { stdio: "ignore" },
    );
  }
  return cli(["status", "--session", op.sessionId], { token: runner.token });
});
await eventually(async () => {
  try {
    await cli(["close", "--session", op.sessionId], { token: runner.token });
    return true;
  } catch {
    return false;
  }
});
await eventually(async () => {
  const r = await cli([
    "environment",
    "delete",
    op.environmentId,
    "--idempotency-key",
    "delete",
  ]);
  return r.phase === "deleted";
});
await cli(["credentials", "revoke", publisher.receipt.id]);
await cli(["whoami"], { token: publisher.token, fail: true });
const events = [];
let cursor = 0;
for (let page = 0; page < 10; page++) {
  const audit = await cli(["audit", "--after", String(cursor)]);
  events.push(...audit.events);
  cursor = audit.next_after;
  if (audit.events.length < 100) break;
}
assert.ok(
  events.some(
    (e) =>
      e.action === "release.publish" &&
      e.outcome === "admitted" &&
      e.target === releaseId,
  ),
);
assert.ok(
  events.some(
    (e) =>
      e.action === "release.activate" &&
      e.outcome === "admitted" &&
      e.target === releaseId,
  ),
);
const report = {
  passed: true,
  serviceId: m.id,
  sessionId: op.sessionId,
  environmentId: op.environmentId,
  checks: [
    "bootstrap rotation, expiry/revocation and service-admin control denial",
    "publish versus activate permissions",
    "revisioned config and pinned compaction",
    "admission pause",
    "queue at zero workers then execute",
    "active tool finishes during scale-down drain",
    "scale 0/1/2; Helm preserves replicas; stale controller detection and restart",
    "API rollout identity",
    "real tool output",
    "explicit sandbox cleanup",
    "management audit",
  ],
};
await file("qualification-kubernetes.json", report);
console.log(JSON.stringify(report, null, 2));
replacementForward?.kill("SIGTERM");
