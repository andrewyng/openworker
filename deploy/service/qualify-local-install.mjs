// Native-installer qualification only: no Helm upgrades and no cloud API calls.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile, writeFile, stat } from "node:fs/promises";
import { resolve, join } from "node:path";
const [directory, binary] = process.argv.slice(2);
if (!directory || !binary)
  throw Error("Usage: qualify-local-install.mjs ROOT OWS");
const root = resolve(directory),
  fixture = join(root, "fixture"),
  bin = resolve(binary);
const m = JSON.parse(await readFile(join(fixture, "kubernetes.json"), "utf8"));
const plan = JSON.parse(
  await readFile(join(root, "state/local-plan.json"), "utf8"),
);
const deploymentScope =
  plan.config.cloud || plan.config.service.fixtureDatabase === false
    ? "cluster"
    : "local";
const tokenFile = await stat(join(root, "state/bootstrap-token"))
  .then(() => join(root, "state/bootstrap-token"))
  .catch(() => join(fixture, "input/admin-token"));
assert.equal(plan.config.service.namespace, m.namespace);
assert.equal(m.scripted, true); // Deterministic provider; effects still run in real OpenShell.
let forward;
async function execute(program, args) {
  return new Promise((ok, no) => {
    const p = spawn(program, args);
    let stdout = "",
      stderr = "";
    const timeout = setTimeout(() => p.kill("SIGTERM"), 180000);
    p.stdout.on("data", (b) => {
      stdout += b;
      if (stdout.length > 8000000) p.kill("SIGTERM");
    });
    p.stderr.on("data", (b) => {
      stderr += b;
      if (stderr.length > 65536) p.kill("SIGTERM");
    });
    p.on("error", no);
    p.on("close", (code) => {
      clearTimeout(timeout);
      if (code !== 0)
        no(
          Error(
            `${program.split("/").at(-1)} failed; inspect scoped resources (output withheld)`,
          ),
        );
      else ok(stdout);
    });
  });
}
async function file(name, data) {
  const path = join(fixture, name);
  await writeFile(
    path,
    typeof data === "string" ? data : JSON.stringify(data),
    { mode: 0o600 },
  );
  return path;
}
const config = await file("installer-cli.json", {
  selected: "local",
  contexts: {
    local: {
      url: m.url,
      namespace: "local",
      tokenFile,
      caFile: join(fixture, "input/api-ca.crt"),
      serviceId: m.id,
    },
  },
});
async function cli(args, token) {
  return execute(bin, [
    "--config",
    config,
    ...(token ? ["--token-file", token] : []),
    ...args,
  ]);
}
const json = async (args, token) =>
  JSON.parse((await cli(args, token)).trim().split("\n").at(-1));
async function eventually(fn, seconds = 150) {
  const end = Date.now() + seconds * 1000;
  let last;
  while (Date.now() < end) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw last ?? Error("Observation deadline exceeded");
}
async function issue(name, role) {
  const token = join(fixture, name + ".token");
  await json([
    "credentials",
    "issue",
    "--spec",
    await file(name + ".json", {
      principal: name,
      role,
      scope: { kind: role === "operator" ? "owned" : "all" },
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    }),
    "--out",
    token,
    "--idempotency-key",
    "install-issue-" + name,
  ]);
  return token;
}
try {
  forward = spawn(
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
  await eventually(() => json(["whoami"]), 20);
  const runner = await issue("runner", "operator"),
    publisher = await issue("publisher", "publisher"),
    releaser = await issue("releaser", "release_administrator");
  await json([
    "profile",
    "deploy",
    "--file",
    await file("installer-profile.json", {
      id: "local@1",
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "shared",
      approvalMode: "bypass",
      allowedPrincipals: ["runner"],
    }),
    "--idempotency-key",
    "install-profile",
  ]);
  await json([
    "profile",
    "activate",
    "local@1",
    "--alias",
    "default",
    "--idempotency-key",
    "install-profile-active",
  ]);
  await json(
    [
      "agent",
      "deploy",
      "--file",
      await file("installer-agent.json", {
        id: "installer@1",
        instructions: "Use the provided tools.",
        tools: ["read_file", "write_file", "run_shell"],
        runtimeProfiles: ["local@1"],
        defaultRuntimeProfile: "local@1",
        allowedPrincipals: ["runner"],
      }),
      "--idempotency-key",
      "install-agent",
    ],
    publisher,
  );
  await json(
    [
      "agent",
      "activate",
      "installer@1",
      "--alias",
      "installer",
      "--idempotency-key",
      "install-agent-active",
    ],
    releaser,
  );
  const submitted = await json(
    [
      "submit",
      "--agent",
      "installer",
      "--project",
      "installer-proof",
      "--message-file",
      await file(
        "installer-task.txt",
        'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"installer-proof.txt","content":"Native installer sandbox proof"}}]',
      ),
      "--idempotency-key",
      "install-submit",
      "--wait",
    ],
    runner,
  );
  const op = submitted.operation;
  assert.ok(op.sessionId && op.runId && op.environmentId);
  assert.equal(
    (
      await json(
        ["watch", "--session", op.sessionId, "--run", op.runId],
        runner,
      )
    ).result.status,
    "completed",
  );
  const run = await json(
    [
      "send",
      "--session",
      op.sessionId,
      "--message-file",
      await file(
        "installer-read.txt",
        'TEST_RESOURCE_GRANTS [{"name":"run_shell","arguments":{"command":"cat installer-proof.txt"}}]',
      ),
      "--idempotency-key",
      "install-read",
    ],
    runner,
  );
  const events = await cli(
    ["watch", "--session", op.sessionId, "--run", run.runId],
    runner,
  );
  assert.ok(events.includes("Native installer sandbox proof"));
  assert.ok(events.includes('"status":"completed"'));
  // A separate installer process must reconcile receipts, not create new revisions.
  const before = JSON.parse(
    await readFile(join(root, "state/journal.json"), "utf8"),
  );
  await execute(bin, [
    "deploy",
    deploymentScope,
    "apply",
    "--state",
    join(root, "state"),
    "--accept-plan",
    plan.digest,
  ]);
  const after = JSON.parse(
    await readFile(join(root, "state/journal.json"), "utf8"),
  );
  assert.deepEqual(after.operations, before.operations);
  let refused = false;
  try {
    await execute(bin, [
      "deploy",
      deploymentScope,
      "destroy",
      "--state",
      join(root, "state"),
      "--deployment-id",
      plan.id,
    ]);
  } catch {
    refused = true;
  }
  assert.ok(refused, "Destroy must refuse active sessions/environments");
  await eventually(async () => {
    await json(["close", "--session", op.sessionId], runner);
    return true;
  });
  await eventually(
    async () =>
      (
        await json([
          "environment",
          "delete",
          op.environmentId,
          "--idempotency-key",
          "install-delete",
        ])
      ).phase === "deleted",
  );
  const report = {
    passed: true,
    serviceId: m.id,
    deploymentId: plan.id,
    mode: plan.config.openshell.mode,
    namespace: m.namespace,
    gatewayNamespace: plan.config.openshell.namespace,
    sessionId: op.sessionId,
    environmentId: op.environmentId,
    checks: [
      "gateway authenticated by ready worker",
      "publish/activate profile and agent using scoped credentials",
      "service submission and real OpenShell file write/shell read",
      "installer restart reconciles original release revisions",
      "teardown refuses active work",
      "session close and explicit sandbox deletion",
    ],
    cloudCalls: false,
    model: "scripted",
  };
  await file("qualification-installer.json", report);
  console.log(JSON.stringify(report, null, 2));
} finally {
  forward?.kill("SIGTERM");
}
