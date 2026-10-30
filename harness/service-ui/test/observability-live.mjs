// Real Rust API + separate PostgreSQL databases. Called by recording_queries.rs.
// Additional browser-only fault/race injection is explicitly identified below.
import { start } from "../server.mjs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile, mkdtemp, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import net from "node:net";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.OW_PLAYWRIGHT_MODULE);
const out = resolve(process.env.OW_OBSERVABILITY_UI_OUTPUT);
await mkdir(out, { recursive: true, mode: 0o700 });
async function containerUI(image) {
  assert.match(image, /^sha256:[a-f0-9]{64}$/);
  const name = `ow-observability-ui-${crypto.randomUUID()}`;
  const label = "openworker.observability-ui-qualification";
  const dir = await mkdtemp(join(tmpdir(), "ow-recording-ui-"));
  const upstream = new URL(process.env.OW_UI_TEST_URL);
  upstream.hostname = "host.docker.internal";
  const probe = net.createServer();
  await new Promise((ok, no) => {
    probe.once("error", no);
    probe.listen(0, "127.0.0.1", ok);
  });
  const port = probe.address().port;
  await new Promise((ok) => probe.close(ok));
  const url = `http://127.0.0.1:${port}`;
  await writeFile(
    join(dir, "ui.json"),
    JSON.stringify({
      host: "0.0.0.0",
      port: 8080,
      publicOrigin: url,
      serviceUrl: upstream.origin,
      allowInternalHttp: true,
      serviceId: "query-service",
      namespace: "test",
    }),
  );
  let owned;
  const close = async () => {
    if (owned) {
      const { stdout } = await exec("docker", ["inspect", owned]);
      assert.equal(JSON.parse(stdout)[0].Config.Labels[label], name);
      await exec("docker", ["rm", "-f", owned]);
    }
    await rm(dir, { recursive: true, force: true });
  };
  try {
    const created = await exec("docker", [
      "create",
      "--pull=never",
      "--name",
      name,
      "--label",
      `${label}=${name}`,
      "--read-only",
      "--cap-drop=ALL",
      "--security-opt=no-new-privileges",
      "--memory=256m",
      "-p",
      `127.0.0.1:${port}:8080`,
      "--mount",
      `type=bind,source=${dir},target=/config,readonly`,
      image,
    ]);
    owned = created.stdout.trim();
    await exec("docker", ["start", owned]);
    for (let i = 0; i < 60; i++) {
      try {
        if ((await fetch(url)).ok) return { url, close };
      } catch {}
      await new Promise((r) => setTimeout(r, 100));
    }
    throw Error("Packaged UI did not become ready");
  } catch (e) {
    await close();
    throw e;
  }
}
const app = process.env.OW_OBSERVABILITY_UI_IMAGE
  ? await containerUI(process.env.OW_OBSERVABILITY_UI_IMAGE)
  : await start({
      port: 0,
      serviceUrl: process.env.OW_UI_TEST_URL,
      serviceId: "query-service",
      namespace: "test",
    });
const disabled = await start({
  port: 0,
  serviceUrl: process.env.OW_UI_DISABLED_URL,
  serviceId: "query-service",
  namespace: "test",
});
let browser;
const checks = [],
  errors = [];
async function login(page, url, token) {
  await page.goto(url + "/#observability");
  await page.locator("#token").fill(token);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.locator("#obs-run-search").waitFor();
}
async function choose(page, text) {
  const input = page.locator("#obs-run-search");
  await input.fill(text);
  const option = page
    .locator(".obs-run-option")
    .filter({ hasText: text })
    .first();
  await option.waitFor();
  await option.click();
  await page.locator(".obs-stats").waitFor();
  await capture(page, "recent-runs");
}
async function capture(page, name) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: join(out, name + ".png"), fullPage: true });
}
async function httpLogin(token) {
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
async function action(user, path, body) {
  const r = await fetch(app.url + "/api" + path, {
    method: "POST",
    headers: {
      Cookie: user.cookie,
      Origin: app.url,
      "Content-Type": "application/json",
      "X-CSRF-Token": user.csrf,
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(body),
  });
  const value = await r.json();
  assert.equal(r.status, 200, JSON.stringify(value));
  return value;
}
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (
      m.type() === "error" &&
      /Content Security Policy|Refused to/.test(m.text())
    )
      errors.push(m.text());
  });
  await login(page, app.url, process.env.OW_UI_USER);
  await page.locator(".obs-stats").waitFor();
  assert(
    (await page.locator("#obs-scope").innerText()).includes(
      "25 recent authorized runs",
    ),
  );
  assert(!(await page.locator("#main").innerText()).includes("Beta Private"));
  assert.equal(await page.locator("#obs-health").count(), 0);
  checks.push(
    "real scoped recent-page summary, no foreign runs or process counters",
  );
  await page.getByRole("button", { name: "Next 25 runs", exact: true }).click();
  await page
    .getByRole("button", { name: "Previous page", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Previous page", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Next 25 runs", exact: true })
    .waitFor();
  checks.push("summary page navigation preserves bounded selected-page scope");
  const input = page.locator("#obs-run-search");
  await input.fill("Alpha % Game");
  await page
    .locator(".obs-run-option")
    .filter({ hasText: "Alpha % Game" })
    .first()
    .waitFor();
  assert.equal(await page.locator(".obs-run-option").count(), 2);
  await capture(page, "run-picker");
  await input.fill("No matching run 游");
  await page.getByText("No matching runs.", { exact: true }).waitFor();
  await input.fill("");
  await page.locator("#obs-search-more:not([hidden])").waitFor();
  const firstCount = await page.locator(".obs-run-option").count();
  await page.locator("#obs-search-more").click();
  await page.waitForFunction(
    (n) => document.querySelectorAll(".obs-run-option").length > n,
    firstCount,
  );
  checks.push(
    "literal percent/Unicode search, empty results, paginated dropdown",
  );
  await input.fill("Deployment");
  await page
    .locator(".obs-run-option")
    .filter({ hasText: "Deployment diagnosis" })
    .waitFor();
  await input.press("ArrowDown");
  assert(await input.getAttribute("aria-activedescendant"));
  await input.press("Enter");
  await page.locator(".obs-stats").waitFor();
  assert(
    (await page.locator(".obs-scope strong").innerText()).includes(
      "Deployment diagnosis",
    ),
  );
  assert(
    (await page.locator(".obs-stats").innerText()).includes("12,500 + unknown"),
  );
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  assert(
    (
      await page.getByRole("table", { name: "Tool summary" }).innerText()
    ).includes("115") === false,
  ); // two separate groups, not duplicated totals
  assert((await page.locator(".obs-stats").innerText()).includes("115"));
  assert(
    (
      await page.getByRole("table", { name: "Model usage" }).innerText()
    ).includes("10,000"),
  );
  assert(
    (
      await page.getByRole("table", { name: "Model usage" }).innerText()
    ).includes("Compaction"),
  );
  await capture(page, "usage-desktop");
  checks.push(
    "keyboard selection, reported/unknown usage, grouped model/tool data",
  );
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  assert.equal(await page.locator(".obs-lane").count(), 100);
  await page
    .getByRole("button", { name: "Load more records", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll(".obs-lane").length === 123,
  );
  await page
    .locator(".obs-lane")
    .filter({ hasText: "fixture" })
    .first()
    .click();
  await page.locator(".obs-step-detail").waitFor();
  assert((await page.locator(".obs-step-detail").innerText()).includes("2 s"));
  await capture(page, "timeline-desktop");
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await page
    .getByRole("button", { name: "coding.read_file", exact: true })
    .click();
  assert.equal(await page.locator(".obs-lane").count(), 76);
  await page
    .getByRole("button", { name: "Clear tool filter", exact: true })
    .click();
  assert.equal(await page.locator(".obs-lane").count(), 123);
  checks.push(
    "watermarked timeline pagination, parallel intervals, step details and tool filtering",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await capture(page, "usage-mobile");
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await capture(page, "timeline-mobile");
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  checks.push("narrow viewport without document overflow");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await choose(page, "Empty Alpha");
  assert((await page.locator(".obs-stats").innerText()).includes("—"));
  assert.equal(await page.locator(".obs-lane").count(), 0);
  await capture(page, "empty-run");
  checks.push("real empty recording remains unknown, no sample fallback");
  // Delay an old search response at the browser boundary; newer results must win.
  let releaseOld, oldSeen;
  const seen = new Promise((r) => (oldSeen = r)),
    release = new Promise((r) => (releaseOld = r));
  await page.route("**/api/v1/observability/runs?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("q") === "Practice") {
      const response = await route.fetch();
      oldSeen();
      await release;
      try {
        await route.fulfill({ response });
      } catch {}
    } else await route.continue();
  });
  await input.fill("Practice");
  await seen;
  await input.fill("Deployment");
  await page
    .locator(".obs-run-option")
    .filter({ hasText: "Deployment diagnosis" })
    .waitFor();
  releaseOld();
  await page.waitForTimeout(120);
  assert.equal(await page.locator(".obs-run-option").count(), 1);
  await input.press("Escape");
  assert.equal(await input.getAttribute("aria-expanded"), "false");
  await page.unroute("**/api/v1/observability/runs?**");
  checks.push(
    "stale search response cannot replace newer results; Escape closes picker",
  );
  // Simulate a busy reader and transport failure, with real successful reads on
  // either side. The real disabled-config path is exercised separately below.
  await choose(page, "Deployment");
  for (const [code, error] of [
    [429, "recording_query_busy"],
    [502, "service_unavailable"],
  ]) {
    await page.route(
      "**/api/v1/sessions/s_ui/observability/*/summary?**",
      (route) =>
        route.fulfill({
          status: code,
          contentType: "application/json",
          body: JSON.stringify({ error }),
        }),
    );
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(".obs-stats").length === 0 &&
        /busy|Unable to/.test(
          document.querySelector("#obs-content").textContent,
        ),
    );
    assert(
      !(await page.locator("#obs-content").innerText()).includes("12,500"),
    );
    await page.unroute("**/api/v1/sessions/s_ui/observability/*/summary?**");
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.locator(".obs-stats").waitFor();
  }
  checks.push(
    "busy/transport errors clear previous metrics and recover on explicit refresh",
  );
  // Explicit browser-only partial-coverage fixture over a real response.
  const partialRoute = "**/api/v1/sessions/s_ui/observability/*/summary?**";
  await page.route(partialRoute, async (route) => {
    const response = await route.fetch();
    const value = await response.json();
    value.summary.coverage.scan_truncated = true;
    value.summary.wall_elapsed_us = null;
    value.summary.coverage.group_truncated = true;
    await route.fulfill({ response, json: value });
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByText("Partial summary", { exact: true }).waitFor();
  await page
    .locator(".obs-details")
    .filter({ has: page.locator("summary", { hasText: "Coverage" }) })
    .locator("summary")
    .click();
  await page
    .getByText("The 4,096-step summary limit was reached.", { exact: true })
    .waitFor();
  await capture(page, "partial-coverage");
  await page.unroute(partialRoute);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.locator(".obs-stats").waitFor();
  checks.push(
    "partial scan/group coverage is visible and expandable; no unbounded totals",
  );
  // Navigation disposes readers and listeners; operational charts still work.
  await page.locator('[data-nav="activity"]').click();
  await page.getByRole("heading", { name: "Sessions by day" }).waitFor();
  await page.locator('[data-nav="observability"]').click();
  await page.locator(".obs-stats").waitFor();
  checks.push("Activity charts preserved; route return resets selection");
  const emptyPage = await browser.newPage();
  await login(emptyPage, disabled.url, process.env.OW_UI_USER);
  await emptyPage
    .getByText(
      "Recordings are unavailable or disabled. You can still search runs.",
      { exact: true },
    )
    .waitFor();
  await emptyPage.locator("#obs-run-search").fill("Deployment");
  await emptyPage
    .locator(".obs-run-option")
    .filter({ hasText: "Deployment diagnosis" })
    .waitFor();
  await capture(emptyPage, "recording-disabled");
  await emptyPage.close();
  checks.push(
    "real disabled recording service still supports authorized run search",
  );
  const admin = await httpLogin(process.env.OW_UI_ADMIN);
  const adminPage = await browser.newPage();
  await login(adminPage, app.url, process.env.OW_UI_ADMIN);
  await adminPage.locator(".obs-stats").waitFor();
  await adminPage.locator("#obs-health summary").click();
  await adminPage
    .getByText(
      "Serving API instance only. Worker and other API counters are not aggregated here.",
      { exact: true },
    )
    .waitFor();
  await capture(adminPage, "instance-health");
  await adminPage.close();
  checks.push("real administrator-only process health without DB credentials");
  const accessToken = crypto.randomUUID().replaceAll("-", "").repeat(2);
  const issued = await action(admin, "/v1/credentials", {
    token: accessToken,
    spec: {
      principal: "alice",
      role: "observer",
      scope: { kind: "owned" },
      expires_at: Math.floor(Date.now() / 1000) + 300,
    },
  });
  const revokedPage = await browser.newPage();
  await login(revokedPage, app.url, accessToken);
  await revokedPage.locator(".obs-stats").waitFor();
  await action(admin, `/v1/credentials/${issued.id}/revoke`, {});
  await revokedPage
    .getByRole("button", { name: "Refresh", exact: true })
    .click();
  await revokedPage.locator("#login:not([hidden])").waitFor();
  assert.equal(await revokedPage.locator("#obs-content").count(), 0);
  await revokedPage.close();
  checks.push("real service revocation signs out and erases metrics");
  assert.equal(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
    0,
  );
  assert.deepEqual(errors, []);
  await writeFile(
    join(out, "qualification-ui.json"),
    JSON.stringify(
      {
        version: 1,
        at: new Date().toISOString(),
        passed: true,
        scope:
          "real local Rust HTTP + UI process + separate PostgreSQL databases; deterministic metadata fixture, no model/tools",
        uiImage: process.env.OW_OBSERVABILITY_UI_IMAGE || null,
        browserVersion: browser.version(),
        browserViewports: [
          { width: 1440, height: 1000 },
          { width: 390, height: 844 },
        ],
        checks,
        consoleErrors: errors,
        screenshots: [
          "usage-desktop",
          "timeline-desktop",
          "usage-mobile",
          "timeline-mobile",
          "empty-run",
          "recording-disabled",
          "instance-health",
          "recent-runs",
          "run-picker",
          "partial-coverage",
        ].map((n) => n + ".png"),
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    `OBSERVABILITY_UI_REPORT ${JSON.stringify({ passed: true, checks: checks.length, screenshots: 10 })}`,
  );
} finally {
  await browser?.close();
  await app.close();
  await disabled.close();
}
