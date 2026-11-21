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
    await page.screenshot({ path: join(directory, `${name}.png`), fullPage: !["eval-case-review", "evals-run-catalog-mobile"].includes(name) });
  };

  await page.goto(`${app.url}/#observability/${session_id}/${turn_id}`);
  await page.locator("#token").fill(process.env.OW_EVALS_TOKEN);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Add to dataset", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add to dataset" });
  const datasetField = dialog.getByLabel("Dataset", { exact: true });
  try {
    await datasetField.fill("Page changes");
  } catch (error) {
    throw Error(await dialog.innerText(), { cause: error });
  }
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
  if (process.env.OW_EVALS_VOLUME) {
    await page.setViewportSize({ width: 1440, height: 1020 });
    await page.goto(`${app.url}/#evals`);
    const tableRows = () => page.locator(".ev-table tbody tr");
    const next = page.getByRole("button", { name: "Next", exact: true });
    await page.getByRole("table", { name: "Evaluation datasets" }).waitFor();
    assert.equal(await tableRows().count(), 16);
    const initial = await tableRows().first().innerText();
    await capture("evals-dataset-catalog");
    await next.click();
    await page.getByText("Page 2 · 16 shown", { exact: true }).waitFor();
    assert.notEqual(await tableRows().first().innerText(), initial);
    await page.getByRole("button", { name: "Previous", exact: true }).click();
    await page.getByText("Page 1 · 16 shown", { exact: true }).waitFor();
    assert.equal(await tableRows().first().innerText(), initial);

    const datasetSearch = page.getByRole("searchbox", { name: "Search datasets" });
    await datasetSearch.fill("Page editor · Release gate");
    await page.getByText("Page 1 · 1 shown", { exact: true }).waitFor();
    await page.getByRole("link", { name: "Page editor · Release gate" }).click();
    await page.getByRole("navigation", { name: "Dataset views" }).getByRole("link", { name: "Runs" }).click();
    await page.getByRole("table", { name: "Evaluation runs" }).waitFor();
    assert.equal(await tableRows().count(), 16);
    assert.equal(await next.isEnabled(), true);
    await next.click();
    await page.getByText("Page 2 · 16 shown", { exact: true }).waitFor();

    await page.goto(`${app.url}/#evals?view=runs`);
    await page.getByRole("table", { name: "Evaluation runs" }).waitFor();
    assert.equal(await tableRows().count(), 16);
    await capture("evals-run-catalog");
    await next.click();
    await page.getByText("Page 2 · 16 shown", { exact: true }).waitFor();
    await tableRows().first().getByRole("link").first().click();
    await page.getByRole("heading", { name: "Evaluation run" }).waitFor();
    await page.goBack();
    await page.getByText("Page 2 · 16 shown", { exact: true }).waitFor();
    await page.getByLabel("Run status").selectOption("queued");
    await page.getByRole("heading", { name: "No matching runs" }).waitFor();
    await page.getByLabel("Run status").selectOption("completed");
    await page.getByLabel("Run date").selectOption("24");
    await page.getByRole("searchbox", { name: "Search runs" }).fill("Page editor · Release gate");
    await page.getByText("Page 1 · 2 shown", { exact: true }).waitFor();
    await tableRows().first().getByRole("link").first().click();
    await page.getByRole("heading", { name: "Evaluation run" }).waitFor();
    await page.goBack();
    await page.getByText("Page 1 · 2 shown", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Run date").inputValue(), "24");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${app.url}/#evals?view=runs`);
    await page.getByRole("table", { name: "Evaluation runs" }).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await capture("evals-run-catalog-mobile");
    console.log("Chrome passed paginated dataset and run catalogs, per-dataset history, filter navigation, and mobile sizing.");
  }
  if (process.env.OW_GUARDRAIL_REDACTED_TURN) {
    await page.setViewportSize({ width: 1440, height: 960 });
    const redaction = JSON.parse(process.env.OW_GUARDRAIL_REDACTED_TURN);
    await page.goto(`${app.url}/#observability/${redaction.session_id}/${redaction.turn_id}`);
    const finding = page.locator(".tr-turns .guardrail-finding");
    await finding.getByText("Credit card rule", { exact: true }).waitFor();
    await page.locator(".tr-context").getByText("Guardrail hit", { exact: true }).waitFor();
    await page.locator(`.tr-session[data-tr-session="${redaction.session_id}"]`)
      .getByText("Guardrail hit", { exact: true }).waitFor();
    assert.equal(await finding.getByText("Redacted", { exact: true }).isVisible(), true);
    await finding.locator("summary").click();
    await finding.getByText("guardrail:builtin.credit-card", { exact: true }).waitFor();
    assert(!(await page.locator("#main").innerText()).includes("4111111111111111"));
    await capture("guardrail-in-trace");

    await page.getByRole("link", { name: "Guardrails", exact: true }).click();
    await page.getByRole("heading", { name: "Guardrails", exact: true }).waitFor();
    await page.getByText("Credit card rule", { exact: true }).waitFor();
    await capture("guardrail-catalog");
    await page.getByLabel("Rule ID", { exact: true }).fill("fixture-ssn");
    await page.getByLabel("Guardrail outcome").selectOption("blocked");
    await page.getByRole("button", { name: "Apply" }).click();
    await page.getByText("Page 1 · 1 shown", { exact: true }).waitFor();
    await page.locator(".guardrail-finding summary").click();
    await page.getByRole("link", { name: "Open session", exact: true }).click();
    await page.getByRole("heading", { name: "Session safety activity" }).waitFor();
    assert.equal(new URL(page.url()).hash, `#observability/${process.env.OW_GUARDRAIL_BLOCKED_SESSION}`);
    assert.equal(await page.locator(".tr-turn-heading").count(), 0);
    console.log("Chrome passed guardrail chips, rule filtering and a block before any turn was created.");
  }
  assert.deepEqual(errors, []);
  console.log("Chrome passed trace capture, on-demand execution, three scoring outcomes and source navigation.");
} finally {
  await browser?.close();
  await app.close();
}
