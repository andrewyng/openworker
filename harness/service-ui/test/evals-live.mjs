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

    await page.goto(`${app.url}/#observability/${redaction.session_id}/${redaction.turn_id}`);
    await page.locator(".tr-turns .guardrail-finding summary").click();
    await page.getByRole("link", { name: "Evaluate matching turns", exact: true }).click();
    await page.getByLabel("Tag", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Tag", { exact: true }).inputValue(), "guardrail:builtin.credit-card");
    await page.getByRole("button", { name: "Preview turns", exact: true }).click();
    await page.getByRole("table", { name: "Matching turns" }).waitFor();
    assert.equal(await page.locator(".ev-target-table tbody tr").count(), 1);
  }
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.goto(`${app.url}/#observability/${session_id}/${turn_id}`);
  await page.getByRole("button", { name: "Turn tags", exact: true }).click();
  const tags = page.getByRole("dialog", { name: "Turn tags" });
  await tags.getByLabel("Tag", { exact: true }).fill("browser-smoke");
  await tags.getByRole("button", { name: "Add tag", exact: true }).click();
  await tags.getByRole("link", { name: "browser-smoke", exact: true }).waitFor();
  await tags.getByRole("button", { name: "Remove tag browser-smoke", exact: true }).click();
  await tags.getByRole("link", { name: "browser-smoke", exact: true }).waitFor({ state: "hidden" });
  await tags.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Session tags", exact: true }).click();
  const sessionTags = page.getByRole("dialog", { name: "Session tags" });
  await sessionTags.getByLabel("Tag", { exact: true }).fill("browser-smoke");
  await sessionTags.getByRole("button", { name: "Add tag", exact: true }).click();
  await sessionTags.getByRole("link", { name: "browser-smoke", exact: true }).waitFor();
  await sessionTags.getByRole("button", { name: "Close", exact: true }).click();
  await page.locator(".tr-context").getByRole("link", { name: "browser-smoke", exact: true }).click();
  await page.getByRole("button", { name: "Preview turns", exact: true }).click();
  await page.getByRole("table", { name: "Matching turns" }).waitFor();
  await page.getByText("Save an evaluator", { exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Browser pricing check");
  await page.getByLabel("Expected text", { exact: true }).fill("/pricing");
  await page.getByRole("button", { name: "Save evaluator", exact: true }).click();
  await page.getByText("Saved Browser pricing check · v1.", { exact: true }).waitFor();
  const failedTurn = JSON.parse(process.env.OW_EVALS_FAILED_TURN);
  for (const selected of [turn_id, failedTurn.turn_id]) {
    await page.locator(".ev-target-table tbody tr")
      .filter({ has: page.locator(`a[href="#observability/${session_id}/${selected}"]`) })
      .getByRole("checkbox").check();
  }
  await page.getByText("2 of 3 selected", { exact: true }).waitFor();
  await capture("eval-tagged-selection");
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.getByRole("button", { name: "Queue evaluation", exact: true }).click();
  await page.getByRole("heading", { name: "Results", exact: true }).waitFor();
  assert.match(new URL(page.url()).hash, /^#evals\/batch\//);
  assert.deepEqual(await page.locator(".ev-metrics strong").allInnerTexts(), ["1", "0", "1"]);
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.locator(".ev-case summary").filter({ hasText: "Passed" }).click();
  await capture("eval-tagged-result");
  await page.getByRole("link", { name: "Open source turn" }).click();
  await page.getByRole("heading", { name: "Evaluation results", exact: true }).waitFor();
  await page.locator(".tr-eval-results").getByRole("link", { name: "Browser pricing check · v1 Passed" }).click();
  await page.getByRole("heading", { name: "Results", exact: true }).waitFor();
  await page.getByRole("link", { name: "Evals", exact: true }).last().click();
  await page.getByRole("searchbox", { name: "Search runs" }).fill("browser-smoke");
  await page.getByText("Page 1 · 1 shown", { exact: true }).waitFor();
  await page.getByRole("table", { name: "Evaluation runs" }).getByRole("link").click();
  await page.getByRole("heading", { name: "Results", exact: true }).waitFor();
  console.log("Chrome passed turn tags, guardrail selection, saving a versioned evaluator, submitting a batch and opening results from Tracing and Evals.");
  await page.locator(".ev-case summary").filter({ hasText: "Error" }).click();
  await page.getByRole("button", { name: "Add to dataset", exact: true }).click();
  const promotion = page.getByRole("dialog", { name: "Add to dataset" });
  await promotion.getByLabel("Dataset", { exact: true }).fill("Provider regressions");
  await promotion.getByText("Case settings", { exact: true }).click();
  await promotion.getByLabel("Case name", { exact: true }).fill("Failed pricing turn");
  assert.equal(await promotion.getByLabel("Expected response", { exact: true }).inputValue(), "/pricing");
  await promotion.getByText("Replay workspace files", { exact: true }).click();
  await promotion.getByRole("button", { name: "Add file", exact: true }).click();
  await promotion.getByLabel("File path", { exact: true }).fill("fixtures/pricing.txt");
  await promotion.getByLabel("File contents", { exact: true }).fill("Original pricing link\n");
  await promotion.getByRole("button", { name: "Save case", exact: true }).click();
  await page.getByRole("heading", { name: "Provider regressions" }).waitFor();
  await page.getByText("Failed pricing turn", { exact: true }).click();
  await page.getByText("Edit or archive case", { exact: true }).click();
  await page.getByLabel("Expected response", { exact: true }).fill("/plans");
  await page.getByText("Replay workspace files", { exact: true }).click();
  await page.getByLabel("File contents", { exact: true }).fill("Revised plans link\n");
  await page.getByRole("button", { name: "Save revision", exact: true }).click();
  await page.getByText("1 case · Version 2", { exact: true }).waitFor();
  const datasetBase = new URL(page.url()).hash;
  await page.getByLabel("Dataset version", { exact: true }).fill("1");
  await page.getByRole("button", { name: "View version", exact: true }).click();
  await page.getByText("1 case · Version 1", { exact: true }).waitFor();
  await page.getByText("Failed pricing turn", { exact: true }).click();
  await page.getByText("/pricing", { exact: true }).waitFor();
  await page.getByText("Workspace fixture · 1 file", { exact: true }).click();
  await page.getByText("Original pricing link", { exact: true }).waitFor();
  assert.equal(await page.getByText("Revised plans link", { exact: true }).count(), 0);
  assert.equal(await page.getByText("Edit or archive case", { exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "Open source evaluation" }).count(), 1);
  await page.getByRole("link", { name: "Back to latest version" }).click();
  await page.getByText("1 case · Version 2", { exact: true }).waitFor();
  await page.getByText("Failed pricing turn", { exact: true }).click();
  await page.getByText("Edit or archive case", { exact: true }).click();
  await page.getByRole("button", { name: "Archive case", exact: true }).click();
  await page.getByText("0 cases · Version 3", { exact: true }).waitFor();
  await page.goto(app.url + "/" + datasetBase + "?version=2");
  await page.getByRole("button", { name: "Run eval", exact: true }).click();
  await page.getByRole("heading", { name: "Results", exact: true }).waitFor();
  assert.deepEqual(await page.locator(".ev-metrics strong").allInnerTexts(), ["0", "0", "1"]);
  await capture("eval-versioned-case");
  console.log("Chrome passed promoting a failed turn, retaining evidence, revising and archiving a case, and scoring its old dataset snapshot.");
  await page.goto(app.url + "/#evals/selection?tag=judge-check");
  await page.getByRole("button", { name: "Preview turns", exact: true }).click();
  await page.getByRole("table", { name: "Matching turns" }).waitFor();
  await page.getByText("Save an evaluator", { exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Browser rubric");
  await page.getByLabel("Evaluator type", { exact: true }).selectOption("judge");
  await page.getByLabel("Judge model profile", { exact: true }).selectOption("judge-fixture");
  await page.getByLabel("Rubric", { exact: true }).fill("Check the expected link.");
  await page.getByRole("button", { name: "Save evaluator", exact: true }).click();
  await page.getByText("Saved Browser rubric · v1.", { exact: true }).waitFor();
  await page.locator(".ev-target-table tbody tr")
    .filter({ has: page.locator(`a[href="#observability/${session_id}/${turn_id}"]`) }).getByRole("checkbox").check();
  await page.getByRole("button", { name: "Preview judge", exact: true }).click();
  await page.getByRole("heading", { name: "Judge preview · Passed", exact: true }).waitFor();
  await capture("eval-judge-preview");
  await page.getByRole("link", { name: "Open preview run" }).click();
  await page.locator(".ev-case summary").click();
  await page.getByText("Judge · fixture-judge · 48 input tokens · 12 output tokens", { exact: true }).waitFor();
  console.log("Chrome passed model judge configuration, single-turn preview, resolved model and reported usage.");

  await page.goto(app.url + "/#evals/replays/" + process.env.OW_EVALS_REPLAY_ID);
  await page.getByRole("table", { name: "Replay comparison" }).waitFor();
  await page.getByText("Regression · 2 fixture files", { exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Open execution" }).count(), 2);
  await capture("eval-replay-comparison");
  await page.goto(app.url + "/#evals/replays/" + process.env.OW_EVALS_INCOMPLETE_REPLAY_ID);
  const incompleteTable=page.getByRole("table", {name:"Replay comparison"});
  await incompleteTable.getByText("Incomplete",{exact:true}).first().waitFor();
  assert.equal(await incompleteTable.getByText("Failed",{exact:true}).count(),0);
  console.log("Chrome passed baseline/candidate comparison and links to both executions.");

  await page.goto(app.url + "/#evals/schedules");
  const periodic=page.locator(".ev-case").filter({hasText:"daily-tags"});
  await periodic.locator("summary").click();
  await periodic.getByRole("link", {name:"Open run"}).waitFor();
  await periodic.getByRole("button", {name:"Pause",exact:true}).click();
  await periodic.getByText("Paused",{exact:true}).waitFor();
  await periodic.locator("summary").click();
  await periodic.getByRole("button", {name:"Resume",exact:true}).click();
  await periodic.getByText("Active",{exact:true}).waitFor();
  await page.getByText("New schedule",{exact:true}).click();
  await page.getByLabel("Name",{exact:true}).fill("Every six hours");
  await page.getByLabel("Tag",{exact:true}).fill("judge-check");
  await page.getByLabel("Evaluator",{exact:true}).selectOption({label:"Plans link · v2"});
  await page.getByLabel("Run every (hours)",{exact:true}).fill("6");
  await page.getByRole("button",{name:"Save schedule",exact:true}).click();
  await page.locator(".ev-case summary").filter({hasText:"Every six hours"}).waitFor();
  await capture("eval-schedules");
  console.log("Chrome passed creating, pausing and resuming schedules with recent run links.");
  assert.deepEqual(errors, []);
  console.log("Chrome passed trace capture, on-demand execution, three scoring outcomes and source navigation.");
} finally {
  await browser?.close();
  await app.close();
}
