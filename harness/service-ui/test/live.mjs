// Invoked by the Rust PostgreSQL integration test. Credentials stay in process memory.
import { start } from "../server.mjs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, mkdtemp, writeFile, rm } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { tmpdir } from "node:os";
import { join } from "node:path";
const exec = promisify(execFile);
async function containerUI() {
  const dir = await mkdtemp(join(tmpdir(), "ow-ui-container-"));
  const url = "http://127.0.0.1:58643",
    name = "ow-ui-acceptance-" + process.pid;
  const upstream = new URL(process.env.OW_UI_TEST_URL);
  upstream.hostname = "host.docker.internal";
  await writeFile(
    join(dir, "ui.json"),
    JSON.stringify({
      host: "0.0.0.0",
      port: 8080,
      publicOrigin: url,
      serviceUrl: upstream.origin,
      allowInternalHttp: true,
      serviceId: "ui-test",
      namespace: "test",
    }),
  );
  await exec("docker", [
    "run",
    "-d",
    "--name",
    name,
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "-p",
    "127.0.0.1:58643:8080",
    "-v",
    dir + ":/config:ro",
    process.env.OW_UI_CONTAINER_IMAGE,
  ]);
  const app = {
    url,
    close: async () => {
      await exec("docker", ["rm", "-f", name]);
      await rm(dir, { recursive: true, force: true });
    },
  };
  try {
    for (let n = 0; n < 40; n++) {
      try {
        if ((await fetch(url)).ok) return app;
      } catch {}
      await new Promise((r) => setTimeout(r, 100));
    }
    throw Error("Container did not start");
  } catch (e) {
    await app.close();
    throw e;
  }
}
const app = process.env.OW_UI_CONTAINER_IMAGE
  ? await containerUI()
  : await start({
      port: 0,
      serviceUrl: process.env.OW_UI_TEST_URL,
      serviceId: "ui-test",
      namespace: "test",
    });
async function login(token) {
  const r = await fetch(app.url + "/login", {
    method: "POST",
    headers: { Origin: app.url, "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  assert.equal(r.status, 200);
  return {
    cookie: r.headers.get("set-cookie").split(";")[0],
    ...(await r.json()),
  };
}
async function request(
  user,
  path,
  method = "GET",
  body,
  key = crypto.randomUUID(),
) {
  const r = await fetch(app.url + "/api" + path, {
    method,
    headers: {
      Cookie: user.cookie,
      Origin: app.url,
      "Content-Type": "application/json",
      "X-CSRF-Token": user.csrf,
      "Idempotency-Key": key,
    },
    body: method === "GET" ? undefined : JSON.stringify(body),
  });
  return { status: r.status, data: await r.json() };
}
let browser;
try {
  const admin = await login(process.env.OW_UI_ADMIN),
    alice = await login(process.env.OW_UI_USER),
    manager = await login(process.env.OW_UI_MANAGER);
  assert.deepEqual(
    (await request(alice, "/v1/sessions")).data.sessions.map((s) => s.id),
    ["s_alice"],
  );
  assert.equal((await request(alice, "/v1/sessions/s_bob")).status, 403);
  assert.equal(
    (await request(alice, "/v1/sessions/s_bob/events/page?after=0")).status,
    403,
  );
  assert.equal((await request(alice, "/v1/releases")).data.entries.length, 1);
  assert.equal((await request(alice, "/v1/profiles")).data.entries.length, 1);
  assert.equal((await request(alice, "/v1/credentials")).status, 403);
  assert.equal(
    (
      await request(manager, "/v1/sessions/s_alice/controls", "POST", {
        runId: process.env.OW_UI_RUN,
        action: { kind: "cancel" },
      })
    ).status,
    403,
  );
  const c = (await request(admin, "/v1/configuration")).data;
  assert.equal(
    (
      await request(alice, "/v1/configuration", "PUT", {
        expectedRevision: c.revision,
        configuration: c.configuration,
      })
    ).status,
    403,
  );
  c.configuration.admissionsPaused = true;
  const write = {
    expectedRevision: c.revision,
    configuration: c.configuration,
  };
  assert.equal(
    (await request(manager, "/v1/configuration", "PUT", write, "config-one"))
      .status,
    200,
  );
  assert.equal(
    (await request(manager, "/v1/configuration", "PUT", write, "config-one"))
      .status,
    200,
  );
  assert.equal(
    (await request(manager, "/v1/configuration", "PUT", write, "config-two"))
      .status,
    409,
  );
  const scale = await request(
    manager,
    "/v1/worker-pools/default/scale",
    "POST",
    { replicas: 2, expectedRevision: 1 },
  );
  assert.equal(scale.status, 202);
  assert.equal(scale.data.desiredReplicas, 2);
  const profile = {
    id: "new@1",
    provider: "openshell",
    cpu: 1,
    memoryMB: 512,
    sharing: "exclusive",
    allowedPrincipals: ["alice"],
  };
  assert.equal(
    (await request(manager, "/v1/profiles", "POST", profile)).status,
    200,
  );
  assert.equal(
    (await request(manager, "/v1/profiles/new@1/activate", "POST", {})).status,
    200,
  );
  const rel = {
    id: "new-agent@1",
    instructions: "Answer accurately.",
    tools: ["read_file"],
    runtimeProfiles: ["new@1"],
    allowedPrincipals: ["alice"],
  };
  assert.equal((await request(admin, "/v1/releases", "POST", rel)).status, 200);
  assert.equal(
    (await request(admin, "/v1/releases/new-agent@1/activate", "POST", {}))
      .status,
    200,
  );
  const events = await request(
    alice,
    "/v1/sessions/s_alice/events/page?after=0",
  );
  assert.equal(events.status, 200);
  assert(events.data.events.length > 0);
  if (process.env.OW_PLAYWRIGHT_MODULE) {
    const { chromium, expect } = createRequire(import.meta.url)(
      process.env.OW_PLAYWRIGHT_MODULE,
    );
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(app.url);
    await page
      .getByLabel("Service token", { exact: true })
      .fill(process.env.OW_UI_USER);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.locator("main h1")).toHaveText("Overview");
    await expect(page.locator("main")).toContainText("Review checkout changes");
    await expect(page.locator("main")).not.toContainText("Private Bob session");
    await mkdir("/tmp/ow-service-ui-live", { recursive: true });
    await page.screenshot({
      path: "/tmp/ow-service-ui-live/overview.png",
      fullPage: true,
    });
    await page.locator('tr[data-session="s_alice"]').click();
    await expect(page.locator("#stream-state")).toHaveText("Live");
    await page
      .getByRole("button", { name: "Review request", exact: true })
      .click();
    await page.getByLabel("Decision", { exact: true }).selectOption("once");
    await page.locator("#save").click();
    await expect(page.locator("#panel")).not.toBeVisible();
    await expect(page.locator("#toast")).toContainText("Control pending");
    await page.getByRole("button", { name: "Cancel run", exact: true }).click();
    await page.locator("#save").click();
    await expect(page.locator("#panel")).not.toBeVisible();
    await expect(page.locator("#toast")).toContainText("Control pending");
    await expect(page.locator("#interactions")).toContainText(
      "Decision pending",
    );
    await page.reload();
    await expect(page.locator("#interactions")).toContainText(
      "Decision pending",
    );
    await expect(
      page.getByRole("button", { name: "Review request", exact: true }),
    ).toHaveCount(0);
    await page.screenshot({
      path: "/tmp/ow-service-ui-live/session.png",
      fullPage: true,
    });
    await page.locator("[data-nav=infrastructure]").click();
    await expect(page.locator("main")).toContainText("standard@1");
    await expect(
      page.getByRole("button", { name: "Scale", exact: true }),
    ).toHaveCount(0);
    await page.locator("#profile-button").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.locator("#login")).toBeVisible();
    await page
      .getByLabel("Service token", { exact: true })
      .fill(process.env.OW_UI_MANAGER);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Scale", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Scale", exact: true }).click();
    await page.getByLabel("Desired workers").fill("3");
    await page.locator("#save").click();
    await expect(page.locator(".pool-count")).toContainText("3");
    await page.locator("#settings-link").click();
    await page.getByRole("button", { name: "Edit configuration" }).click();
    const config = JSON.parse(
      await page.getByLabel("Configuration", { exact: true }).inputValue(),
    );
    config.admissionsPaused = false;
    await page
      .getByLabel("Configuration", { exact: true })
      .fill(JSON.stringify(config));
    const attempts = [];
    await page.route("**/api/v1/configuration", async (route) => {
      if (route.request().method() !== "PUT") {
        await route.continue();
        return;
      }
      attempts.push({
        key: route.request().headers()["idempotency-key"],
        body: route.request().postData(),
      });
      const response = await route.fetch();
      if (attempts.length === 1)
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ error: "outcome_unknown" }),
        });
      else await route.fulfill({ response });
    });
    await page.locator("#save").click();
    await expect(page.locator("#save")).toHaveText("Retry same request");
    await expect(
      page.getByLabel("Configuration", { exact: true }),
    ).toBeDisabled();
    await page.locator("#save").click();
    await expect(page.locator("main")).toContainText("Open");
    assert.equal(attempts.length, 2);
    assert.deepEqual(attempts[0], attempts[1]);
    await page.unroute("**/api/v1/configuration");
    await page.getByRole("button", { name: "Access", exact: true }).click();
    await expect(page.locator("main")).toContainText("alice");
    await page
      .getByRole("button", { name: "Issue token", exact: true })
      .click();
    await page.getByLabel("Principal", { exact: true }).fill("browser-issued");
    await page.locator("#save").click();
    await expect(page.locator("main")).toContainText("browser-issued");
    const row = page.locator("tr").filter({ hasText: "browser-issued" });
    await row.getByRole("button", { name: "Revoke" }).click();
    await page.locator("#save").click();
    await expect(
      page.locator("tr").filter({ hasText: "browser-issued" }),
    ).toContainText("Revoked");
    await page.locator("#profile-button").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page
      .getByLabel("Service token", { exact: true })
      .fill(process.env.OW_UI_USER);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.locator("[data-nav=overview]").click();
    await page
      .getByRole("button", { name: "New session", exact: true })
      .click();
    await page.getByLabel("Task", { exact: true }).fill("Review this project.");
    await page.locator("#panel details summary").click();
    await page
      .getByLabel("Runtime profile", { exact: true })
      .selectOption("new@1");
    await page.locator("#save").click();
    await expect(page.locator("main h1")).toHaveText("Session provisioning");
    await expect(page.locator("main")).toContainText("queued");
    const submissionUrl = page.url();
    await page.reload();
    await expect(page.locator("main h1")).toHaveText("Session provisioning");
    assert.equal(page.url(), submissionUrl);
    await page.setViewportSize({ width: 390, height: 850 });
    await page.goto(app.url + "/#overview");
    await expect(page.locator("main h1")).toHaveText("Overview");
    assert(
      !(await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      )),
    );
    await page.screenshot({
      path: "/tmp/ow-service-ui-live/mobile.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "Browser: login, scoped sessions/SSE, approval/cancel receipts after reload, same-key lost-response retry, role controls, scale, config, issue/revoke, queued submission/reload, logout, mobile passed.",
    );
  }
  const cancel = await request(alice, "/v1/sessions/s_alice/controls", "POST", {
    runId: process.env.OW_UI_RUN,
    action: { kind: "cancel" },
  });
  assert.equal(cancel.status, process.env.OW_PLAYWRIGHT_MODULE ? 409 : 202);
  console.log(
    "PostgreSQL/API/UI: scoped listing, catalog, event pages, role separation, revision conflicts, idempotency replay, releases/profiles, controls passed.",
  );
} finally {
  await browser?.close();
  await app.close();
}
