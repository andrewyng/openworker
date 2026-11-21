import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { start } from "../server.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.OW_PLAYWRIGHT_MODULE);
const app = await start({
  port: 0,
  serviceUrl: process.env.OW_EVALS_URL,
  serviceId: "eval-qualification",
  namespace: "test",
});
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.OW_EVALS_CHROME,
  });
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  page.setDefaultTimeout(10_000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy|Refused to/.test(m.text()))
      errors.push(m.text());
  });
  const { session_id, turn_id } = JSON.parse(process.env.OW_EVALS_PASS_TURN);
  const capture = async (name) => {
    const directory = process.env.OW_EVALS_SCREENSHOTS;
    if (!directory) return;
    await mkdir(directory, { recursive: true });
    await page.screenshot({ path: join(directory, `${name}.png`), fullPage: name !== "eval-case-review" });
  };

  await page.goto(`${app.url}/#observability/${session_id}/${turn_id}`);
  await page.locator("#token").fill(process.env.OW_EVALS_TOKEN);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Add to dataset", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add to dataset" });
  await dialog.getByLabel("Dataset", { exact: true }).fill("Page changes");
  await dialog.getByRole("textbox", { name: "Expected response" }).fill('href="/pricing"');
  assert.equal(await dialog.getByText("Field Notes", { exact: false }).isVisible(), false);
  await dialog.getByText("Review captured evidence").click();
  assert.equal(await dialog.getByText("Field Notes", { exact: false }).isVisible(), true);
  assert.equal(await dialog.getByRole("checkbox").isChecked(), false);
  await capture("eval-case-review");
  await dialog.getByText("Case settings").click();
  await dialog.getByLabel("Case name", { exact: true }).fill("Set the Plans destination");
  await dialog.getByRole("button", { name: "Save case" }).click();
  await page.getByRole("heading", { name: "Page changes" }).waitFor();
  await page.getByText("Set the Plans destination", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Run eval" }).click();
  await page.getByRole("heading", { name: "Evaluation run" }).waitFor();
  await page.locator(".ev-pill").getByText("Completed", { exact: true }).waitFor();
  assert.equal(await page.locator(".ev-metrics strong").first().innerText(), "1");

  const { OW_EVALS_DATASET: dataset, OW_EVALS_JOB: job } = process.env;
  await page.goto(`${app.url}/#evals/${dataset}/${job}`);
  await page.getByRole("heading", { name: "Results" }).waitFor();
  assert.deepEqual(await page.locator(".ev-metrics strong").allInnerTexts(), ["1", "1", "1"]);
  assert.equal(await page.getByRole("link", { name: "Open source turn" }).count(), 0);
  await capture("eval-run-results");
  await page.locator(".ev-case summary").last().click();
  await page.getByText("Final response was unavailable for scoring.").waitFor();
  await page.getByRole("link", { name: "Open source turn" }).waitFor();
  await capture("eval-result-detail");
  await page.getByRole("link", { name: "Open source turn" }).click();
  await page.getByRole("heading", { name: "Tracing", exact: true }).waitFor();
  assert.match(new URL(page.url()).hash, /^#observability\/.+\/.+$/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${app.url}/#evals/${dataset}/${job}`);
  await page.getByRole("heading", { name: "Results" }).waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await capture("eval-run-mobile");
  assert.deepEqual(errors, []);
  console.log("Chrome passed trace capture, on-demand execution, three scoring outcomes and source navigation.");
} finally {
  await browser?.close();
  await app.close();
}
