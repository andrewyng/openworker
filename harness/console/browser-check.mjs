// Optional Playwright QA. Install/use Playwright separately; no runtime dependency.
import { createRequire } from "node:module";
import { readFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.OW_PLAYWRIGHT_MODULE || "playwright");
const root = resolve(process.argv[2]),
  config = JSON.parse(await readFile(join(root, "console.json"), "utf8"));
const browser = await chromium.launch({
  headless: true,
  ...(process.env.OW_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.OW_CHROMIUM_EXECUTABLE }
    : {}),
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await mkdir(join(root, "screenshots"), { recursive: true, mode: 0o700 });
try {
  await page.goto(`http://127.0.0.1:${config.port}/`);
  await page
    .getByLabel("Console access key")
    .fill((await readFile(config.access_key_file, "utf8")).trim());
  await page.getByRole("button", { name: "Open console" }).click();
  await page.getByRole("heading", { name: "Session explorer" }).waitFor();
  await page
    .getByRole("button", { name: /Weekly release brief/ })
    .first()
    .click();
  await page.getByRole("tab", { name: /Artifacts/ }).click();
  await page.getByText("release-brief.txt", { exact: true }).waitFor();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download ↓" }).first().click();
  const download = await downloadPromise;
  assert.equal(await download.failure(), null);
  await page.screenshot({
    path: join(root, "screenshots", "console-artifacts.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Review the deployment checklist/ })
    .first()
    .click();
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.getByText("DECISION NEEDED", { exact: true }).waitFor();
  await page.screenshot({
    path: join(root, "screenshots", "console-session.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: /Activity/ }).click();
  await page.locator(".event").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: join(root, "screenshots", "console-mobile.png"),
    fullPage: false,
  });
  const overflow = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
    items: [...document.querySelectorAll("body *")]
      .filter((e) => e.getBoundingClientRect().right > innerWidth + 1)
      .map((e) => ({
        tag: e.tagName,
        cls: e.className,
        right: e.getBoundingClientRect().right,
      }))
      .slice(0, 8),
  }));
  assert.ok(overflow.scroll <= overflow.width, JSON.stringify(overflow));
  assert.equal(await page.locator(".wordmark").count(), 1);
  await page.locator("#panel-activity").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: join(root, "screenshots", "console-mobile-activity.png"),
  });
  assert.deepEqual(errors, []);
  console.log(
    "PASS console browser: login, real sessions, approvals, events, download, mobile layout and no JS errors",
  );
  if (config.grafana_url) {
    const g = new URL(config.grafana_url).origin;
    const monitoring = await browser.newContext({
      viewport: { width: 1440, height: 1100 },
    });
    await monitoring.route("**/*", (route) =>
      new URL(route.request().url()).origin === g
        ? route.continue()
        : route.abort(),
    );
    const password = (
      await readFile(join(root, "private/grafana-password"), "utf8")
    ).trim();
    const login = await monitoring.request.post(g + "/login", {
      data: { user: "admin", password },
    });
    assert.equal(login.status(), 200);
    const dashboard = await monitoring.newPage();
    await dashboard.goto(config.grafana_url);
    await dashboard.getByText("Operations / second", { exact: true }).waitFor();
    await dashboard
      .getByText("Telemetry export failures", { exact: true })
      .waitFor();
    await dashboard.screenshot({
      path: join(root, "screenshots", "grafana-dashboard.png"),
      fullPage: true,
    });
    console.log(
      "PASS authenticated Grafana dashboard renders provisioned panels",
    );
    await monitoring.close();
  }
} finally {
  await browser.close();
}
