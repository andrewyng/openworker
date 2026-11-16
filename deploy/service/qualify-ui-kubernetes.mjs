// Browser -> UI pod -> Rust API -> PostgreSQL -> worker -> real OpenShell tools.
// Scripted model, no paid inference. Deliberately do not auto-retry mutations.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import {
  randomBytes,
  randomUUID,
  createHash,
  X509Certificate,
} from "node:crypto";
import https from "node:https";
import { run } from "./stack.mjs";
const root = resolve(process.argv[2] || ""),
  m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
assert(m.context.startsWith("kind-") && !m.destroyed);
const ca = await readFile(join(root, "input/api-ca.crt")),
  cert = new X509Certificate(await readFile(join(root, "input/api.crt")));
const spki = createHash("sha256")
  .update(cert.publicKey.export({ type: "spki", format: "der" }))
  .digest("base64");
const admin = (await readFile(join(root, "input/admin-token"), "utf8")).trim();
const resumeCancellation = process.argv[3] === "--resume-cancellation";
const resume = process.argv[3] === "--resume" || resumeCancellation;
const report = join(root, "qualification-ui.json");
const runner = resume
  ? (await readFile(join(root, "ui-runner.token"), "utf8")).trim()
  : randomBytes(32).toString("hex");
const record = resume
  ? JSON.parse(await readFile(report, "utf8"))
  : {
      started: new Date().toISOString(),
      passed: false,
      serviceId: m.id,
      namespace: m.namespace,
      checks: [],
    };
if (resume) {
  assert.equal(record.serviceId, m.id);
  assert(record.sessionId && record.environmentId && !record.passed);
  assert.equal(
    record.phase,
    resumeCancellation
      ? "Cancelling a real in-flight sandbox command"
      : "Restarting UI while real shell work is running",
  );
  delete record.error;
} else
  try {
    await readFile(report);
    throw Error(
      "Qualification already has a journal; inspect it before another run.",
    );
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
const checkpoint = async (label) => {
  record.phase = label;
  await writeFile(report, JSON.stringify(record, null, 2), { mode: 0o600 });
  console.log(label);
};
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
let browser, apiForward, uiForward;
async function forward(service, port) {
  return new Promise((ok, no) => {
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
        "service/" + service,
        port + ":443",
        "--address",
        "127.0.0.1",
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    let ready = false;
    const timer = setTimeout(() => {
      p.kill();
      no(Error("Port-forward timeout"));
    }, 30000);
    p.stdout.on("data", (b) => {
      if (!ready && b.toString().includes("Forwarding from")) {
        ready = true;
        clearTimeout(timer);
        ok(p);
      }
    });
    p.stderr.resume();
    p.on("exit", () => {
      clearTimeout(timer);
      if (!ready) no(Error("Port-forward stopped before readiness"));
    });
    p.on("error", no);
  });
}
async function stopForward(p) {
  if (!p || p.exitCode !== null) return;
  await new Promise((r) => {
    p.once("exit", r);
    p.kill("SIGTERM");
  });
}
async function api(
  path,
  method = "GET",
  body,
  token = admin,
  key = randomUUID(),
) {
  return new Promise((ok, no) => {
    const q = https.request(
      m.url + path,
      {
        method,
        ca,
        headers: {
          Authorization: "Bearer " + token,
          "x-openworker-namespace": "local",
          "x-openworker-service-id": m.id,
          "content-type": "application/json",
          "idempotency-key": key,
        },
      },
      (r) => {
        let data = "";
        r.on("data", (b) => (data += b));
        r.on("end", () => {
          try {
            const value = JSON.parse(data);
            if (!r.statusCode || r.statusCode >= 300)
              throw Error(
                `API ${method} ${path}: ${r.statusCode} ${JSON.stringify(value)}`,
              );
            assert.equal(r.headers["x-openworker-service-id"], m.id);
            ok(value);
          } catch (e) {
            no(e);
          }
        });
      },
    );
    q.on("error", no);
    q.setTimeout(10000, () => q.destroy(Error("API timeout")));
    q.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
async function until(fn, seconds = 120) {
  let last;
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw last || Error("Timed out: " + record.phase);
}
const snapshot = () =>
  api("/v1/sessions/" + record.sessionId, "GET", undefined, runner);
const events = async () => {
  const out = [];
  let after = 0;
  for (let n = 0; n < 10; n++) {
    const p = await api(
      `/v1/sessions/${record.sessionId}/events/page?after=${after}`,
      "GET",
      undefined,
      runner,
    );
    if (!p.events.length) break;
    out.push(...p.events);
    after = p.events.at(-1).event_id;
    if (p.events.length < 500) break;
  }
  return out;
};
try {
  await checkpoint("Starting verified local TLS connections");
  apiForward = await forward("ows-api", 58444);
  uiForward = await forward("ow-ui-ingress", 58644);
  await api("/v1/auth/me");
  let credential = { id: record.runnerCredentialId };
  if (!resume) {
    credential = await api(
      "/v1/credentials",
      "POST",
      {
        token: runner,
        spec: {
          principal: "ui-runner",
          role: "operator",
          scope: { kind: "owned" },
          expires_at: Math.floor(Date.now() / 1000) + 3600,
        },
      },
      admin,
      "ui-runner-credential",
    );
    record.runnerCredentialId = credential.id;
    await writeFile(join(root, "ui-runner.token"), runner, { mode: 0o600 });
  }
  const { chromium, expect } = createRequire(import.meta.url)(
    process.env.OW_PLAYWRIGHT_MODULE,
  );
  browser = await chromium.launch({
    headless: true,
    args: ["--ignore-certificate-errors-spki-list=" + spki],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mkdir(join(root, "screenshots"), { recursive: true });
  async function login(token) {
    await page.getByLabel("Service token", { exact: true }).fill(token);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.locator("#app")).toBeVisible();
  }
  async function logout() {
    await page.locator("#profile-button").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.locator("#login")).toBeVisible();
  }
  async function save() {
    await page.locator("#save").click();
    await expect(page.locator("#panel")).not.toBeVisible();
  }
  await page.goto(
    "https://localhost:58644" + (resume ? "#sessions/" + record.sessionId : ""),
  );
  await login(resume ? runner : admin);
  const cookie = (await page.context().cookies()).find(
    (c) => c.name === "__Host-ow_ui",
  );
  assert(cookie?.secure && cookie.httpOnly && cookie.sameSite === "Strict");
  if (!resume) {
    await checkpoint("Publishing RuntimeProfile and agent through the browser");
    await page.locator("[data-nav=infrastructure]").click();
    await page
      .getByRole("button", { name: "New profile", exact: true })
      .click();
    const profile = {
      id: "ui-ask@1",
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "exclusive",
      approvalMode: "ask",
      allowedPrincipals: ["ui-runner"],
    };
    await page
      .getByLabel("Definition", { exact: true })
      .fill(JSON.stringify(profile));
    await save();
    await page.locator('[data-action="activate-profile:ui-ask@1"]').click();
    await save();
    await page.locator("[data-nav=agents]").click();
    await page
      .getByRole("button", { name: "Publish release", exact: true })
      .click();
    const agent = {
      id: "ui-builder@1",
      instructions: "Complete the requested task using the provided tools.",
      tools: ["read_file", "write_file", "run_shell"],
      runtimeProfiles: ["ui-ask@1"],
      defaultRuntimeProfile: "ui-ask@1",
      allowedPrincipals: ["ui-runner"],
    };
    await page
      .getByLabel("Definition", { exact: true })
      .fill(JSON.stringify(agent));
    await save();
    await page.locator('[data-action="activate-release:ui-builder@1"]').click();
    await save();
    record.checks.push("Profile/release publication and activation through UI");
    await logout();
    await login(runner);
    await expect(
      page.getByRole("button", { name: "Publish release", exact: true }),
    ).toHaveCount(0);
    const task =
      "TEST_RESOURCE_GRANTS " +
      JSON.stringify([
        {
          name: "run_shell",
          arguments: {
            command:
              "printf 'M5_UI_PROOF\\n' >> ui-proof.txt; sleep 12; cat ui-proof.txt",
          },
        },
        { name: "read_file", arguments: { path: "ui-proof.txt" } },
      ]);
    await checkpoint(
      "Submitting task and provisioning real OpenShell environment",
    );
    await page.locator('[data-action="run:ui-builder@1"]').click();
    await page.getByLabel("Task", { exact: true }).fill(task);
    await save();
    await page.waitForURL(/#operation\//);
    record.operationId = page.url().split("#operation/")[1];
    await checkpoint("Waiting for worker admission");
    const op = await until(async () => {
      const o = await api(
        "/v1/operations/" + record.operationId,
        "GET",
        undefined,
        runner,
      );
      if (o.status === "failed") throw Error(o.error);
      return o.status === "complete" && o;
    }, 180);
    record.sessionId = op.sessionId;
    record.environmentId = op.environmentId;
    await page.waitForURL(/#sessions\//, { timeout: 30000 });
    const waiting = await until(async () => {
      const s = await snapshot();
      return s.interactions.length && s;
    });
    assert.equal(waiting.session.sandbox, "openshell");
    assert.equal(waiting.session.permissions.mode, "ask");
    assert.equal(waiting.mode, "scripted-test");
    record.firstRunId = waiting.turn.id;
    record.approvalId = waiting.interactions[0].id;
    record.environmentBinding = waiting.session.environment;
    assert.equal(waiting.session.environment.spec.provider, "openshell-k8s");
    await page.reload();
    await expect(page.locator("#interactions")).toContainText(
      "Approval needed",
    );
    assert.equal((await snapshot()).interactions[0].id, record.approvalId);
    await checkpoint("Restarting API while the approval is parked");
    await stopForward(apiForward);
    apiForward = null;
    await kube(["rollout", "restart", "deployment/ows-api"]);
    await kube(["rollout", "status", "deployment/ows-api", "--timeout=120s"]);
    apiForward = await forward("ows-api", 58444);
    assert.equal((await snapshot()).interactions[0].id, record.approvalId);
    await expect(page.locator("#stream-state")).toHaveText("Live", {
      timeout: 30000,
    });
    await page
      .getByRole("button", { name: "Review request", exact: true })
      .click();
    await page.getByLabel("Decision", { exact: true }).selectOption("once");
    await save();
    await until(async () =>
      (await events()).some(
        (e) => e.turn_id === record.firstRunId && e.type === "tool_started",
      ),
    );
    await checkpoint("Restarting UI while real shell work is running");
    await kube(["rollout", "restart", "deployment/ows-ui"]);
    await kube(["rollout", "status", "deployment/ows-ui", "--timeout=120s"]);
    await page.reload();
    await expect(page.locator("#login")).toBeVisible();
    await login(runner);
    await expect(page.locator("main h1")).not.toHaveText("Unable to load");
  }
  if (!resumeCancellation) {
    const done = await until(async () => {
      const s = await snapshot();
      if (s.turn.status === "waiting" && s.interactions.length) {
        const i = s.interactions[0];
        assert.equal(i.call.name, "read_file");
        assert.equal(JSON.parse(i.call.arguments).path, "ui-proof.txt");
        if (!(s.controls || []).some((c) => c.target === "response:" + i.id)) {
          await expect(page.locator("#interactions")).toContainText(
            "Approval needed",
          );
          await page
            .getByRole("button", { name: "Review request", exact: true })
            .click();
          await save();
        }
        return false;
      }
      assert.notEqual(s.turn.status, "failed");
      return s.turn.status === "completed" && s;
    });
    assert.equal(done.session.id, record.sessionId);
    assert.deepEqual(done.session.environment, record.environmentBinding);
    let ev = await events();
    const first = ev.filter((e) => e.turn_id === record.firstRunId);
    assert.equal(
      first.filter(
        (e) => e.type === "tool_started" && e.data.name === "run_shell",
      ).length,
      1,
    );
    assert(
      first.some(
        (e) =>
          e.type === "tool_finished" &&
          JSON.stringify(e.data.result).includes("M5_UI_PROOF"),
      ),
    );
    record.checks.push(
      "Real OpenShell shell/file effects; approval identity survives API rollout; UI restart requires login and preserves execution",
    );
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.locator(".session-meta .status")).toHaveText("completed");
    await page.screenshot({
      path: join(root, "screenshots", "completed.png"),
      fullPage: true,
    });
    async function sendTask(command) {
      await page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      await page
        .getByLabel("Message", { exact: true })
        .fill(
          "TEST_RESOURCE_GRANTS " +
            JSON.stringify([{ name: "run_shell", arguments: { command } }]),
        );
      await save();
      const s = await until(async () => {
        const s = await snapshot();
        return s.interactions.length && s;
      });
      await expect(page.locator("#interactions")).toContainText(
        "Approval needed",
      );
      await page
        .getByRole("button", { name: "Review request", exact: true })
        .click();
      await save();
      return s.turn.id;
    }
    await checkpoint("Verifying the persisted file and exactly one append");
    const checkRun = await sendTask(
      "printf 'ui-lines='; wc -l < ui-proof.txt; cat ui-proof.txt",
    );
    await until(async () => (await snapshot()).turn.status === "completed");
    ev = await events();
    assert(
      ev.some(
        (e) =>
          e.turn_id === checkRun &&
          e.type === "tool_finished" &&
          JSON.stringify(e.data.result).includes("ui-lines=1"),
      ),
    );
    record.checks.push(
      "Independent sandbox read confirms one persisted append",
    );
    await checkpoint("Cancelling a real in-flight sandbox command");
    const cancelRun = await sendTask(
      "printf started > cancel-proof.txt; sleep 30; printf done >> cancel-proof.txt",
    );
    record.cancelRunId = cancelRun;
    await until(async () =>
      (await events()).some(
        (e) => e.turn_id === cancelRun && e.type === "tool_started",
      ),
    );
    await page.getByRole("button", { name: "Cancel run", exact: true }).click();
    await save();
  }
  const cancelled = await until(async () => {
    const s = await snapshot();
    return ["cancelled", "interrupted"].includes(s.turn.status) && s;
  });
  assert.equal(cancelled.turn.id, record.cancelRunId);
  assert(
    cancelled.controls.some(
      (c) =>
        c.target === "cancel:" + record.cancelRunId && c.status === "applied",
    ),
  );
  const cancelEvents = (await events()).filter(
    (e) => e.turn_id === record.cancelRunId,
  );
  assert.equal(
    cancelEvents.filter((e) => e.type === "tool_started").length,
    1,
    "Cancelled remote action must not be replayed",
  );
  if (cancelled.turn.status === "interrupted")
    assert(
      cancelEvents.some(
        (e) => e.type === "turn_completed" && e.data.outcome_unknown_count > 0,
      ),
      "Interrupted cancellation must expose tool-outcome uncertainty",
    );
  record.cancellationStatus = cancelled.turn.status;
  if (cancelled.turn.status === "interrupted") {
    const values = JSON.parse(
      await readFile(join(root, "values.json"), "utf8"),
    );
    const schema = values.config.schema;
    assert(/^ow_[a-z0-9_]+$/.test(schema));
    assert(/^s_[a-f0-9]+$/.test(record.sessionId));
    const count = await kube([
      "exec",
      "deployment/postgres",
      "--",
      "psql",
      "-U",
      "openworker",
      "-d",
      "openworker",
      "-Atc",
      `SELECT count(*) FROM ${schema}.history WHERE session_id='${record.sessionId}' AND data->>'type'='function_call_output' AND data->>'output' LIKE '%outcome_unknown%'`,
    ]);
    assert(Number(count.trim()) > 0);
    record.unknownOutcomeInModelHistory = true;
  }

  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.locator(".session-meta .status")).toHaveText(
    cancelled.turn.status,
  );
  await page.screenshot({
    path: join(root, "screenshots", "cancelled.png"),
    fullPage: true,
  });
  record.checks.push(
    "Browser cancellation applied; unknown remote outcome explicitly interrupted, no tool replay",
  );
  await checkpoint("Scaling workers through the management UI");
  await logout();
  await login(admin);
  await page.locator("[data-nav=infrastructure]").click();
  await page.getByRole("button", { name: "Scale", exact: true }).click();
  await page.getByLabel("Desired workers").fill("2");
  await save();
  await until(async () => {
    const p = (await api("/v1/worker-pools")).pools[0];
    return (
      p.desiredReplicas === 2 &&
      p.observed.readyReplicas === 2 &&
      p.observed.status === "applied"
    );
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.screenshot({
    path: join(root, "screenshots", "workers.png"),
    fullPage: true,
  });
  record.checks.push(
    "UI scale request reconciled to two ready Kubernetes workers",
  );
  assert.deepEqual(errors, []);
  await checkpoint(
    "Closing session and deleting only its OpenShell environment",
  );
  await api(
    `/v1/sessions/${record.sessionId}/close`,
    "POST",
    {},
    runner,
    "ui-close",
  );
  await api(
    `/v1/environments/${record.environmentId}/delete`,
    "POST",
    {},
    admin,
    "ui-delete",
  );
  await until(
    async () =>
      (await api("/v1/environments/" + record.environmentId)).environment
        .phase === "deleted",
  );
  await api(
    "/v1/credentials/" + credential.id + "/revoke",
    "POST",
    {},
    admin,
    "ui-revoke",
  );
  record.checks.push(
    "Session close, remote environment deletion, and runner credential revocation",
  );
  record.passed = true;
  record.finished = new Date().toISOString();
  await checkpoint("Complete-stack UI qualification passed");
  console.log(
    JSON.stringify({
      passed: true,
      sessionId: record.sessionId,
      environmentId: record.environmentId,
      report,
    }),
  );
} catch (e) {
  record.error = String(e.message)
    .replaceAll(admin, "[credential]")
    .replaceAll(runner, "[credential]");
  await writeFile(report, JSON.stringify(record, null, 2), { mode: 0o600 });
  console.error(record.error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stopForward(apiForward);
  await stopForward(uiForward);
}
