import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { modelFixture } from "./model-fixture.mjs";
const root = await mkdtemp(join(tmpdir(), "arcade-review-ui-")),
  fixture = await modelFixture();
let app, page;
async function launch() {
  app = await electron.launch({
    ...(process.env.ARCADE_PACKAGED_APP
      ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
      : { args: [resolve(".")] }),
    env: {
      ...process.env,
      ARCADE_DATA_DIR: root,
      OPENWORKER_RUNTIME:
        process.env.OPENWORKER_RUNTIME ??
        resolve("../../target/release/openworker-harness"),
    },
    timeout: 60000,
  });
  page = await app.firstWindow();
  page.setDefaultTimeout(25000);
}
try {
  await launch();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Ollama Local", exact: true }).click();
  await page.getByLabel("Model ID", { exact: true }).fill("fixture-game");
  await page.getByText("Connection details", { exact: true }).click();
  await page
    .getByLabel("Base URL", { exact: true })
    .fill(fixture.provider.baseUrl);
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page
    .getByLabel("Game idea", { exact: true })
    .fill("A tiny star catching game.");
  await page
    .getByRole("button", { name: "Build my game", exact: true })
    .click();
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page
    .getByText(
      "Your game is ready to play. Catch stars and try for a new high score!",
      { exact: true },
    )
    .waitFor();
  const published = await page.locator("#game-preview").getAttribute("src");
  fixture.state.mode = "review";
  await page
    .getByLabel("Change your game", { exact: true })
    .fill("Make a new version, inspect it and report what was tested.");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page
    .getByRole("button", { name: "Compare update", exact: true })
    .click();
  await page
    .frameLocator("#comparison-preview")
    .getByText("Version 2 · Catch a little joy", { exact: true })
    .waitFor();
  const f = page
    .frames()
    .find((f) => f.url().includes("review-") && f.url().includes(".draft/"));
  await f.evaluate(() =>
    window.arcadeSave.save("comparison", 1, { played: true }),
  );
  const saved = await page.evaluate(
    (url) =>
      window.arcade.gameProgress(url, {
        op: "load",
        slot: "comparison",
        version: 1,
      }),
    published,
  );
  assert.equal(saved, null, "comparison must not change real player saves");
  await page.getByRole("button", { name: "Current", exact: true }).click();
  await page
    .frameLocator("#comparison-preview")
    .getByText("Version 1 · Catch a little joy", { exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Proposed update", exact: true })
    .click();
  await page.locator(".testing-summary summary").click();
  await page
    .locator(".check-passed")
    .getByText("Passed", { exact: true })
    .waitFor();
  assert.equal(await page.locator(".check-failed").innerText(), "Failed");
  assert.equal(await page.locator(".check-not_tested").count(), 3);
  await mkdir("test-results/review", { recursive: true });
  await page.screenshot({ path: "test-results/review/comparison.png" });
  await app.close();
  app = undefined;
  await launch();
  await page.getByRole("button", { name: /^Continue building/ }).click();
  await page
    .getByRole("button", { name: "Compare update", exact: true })
    .click();
  await page
    .frameLocator("#comparison-preview")
    .getByText("Version 2 · Catch a little joy", { exact: true })
    .waitFor();
  await page.locator(".testing-summary summary").click();
  assert.equal(await page.locator(".check-failed").innerText(), "Failed");
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page
    .frameLocator("#game-preview")
    .getByText("Version 2 · Catch a little joy", { exact: true })
    .waitFor();
  assert.equal(await page.locator("#comparison-preview").count(), 0);
  await page.getByRole("button", { name: "Versions", exact: true }).click();
  await page
    .locator(".versions-panel button")
    .filter({ hasText: /Version 1/ })
    .click();
  await page
    .frameLocator("#game-preview")
    .getByText("Version 1 · Catch a little joy", { exact: true })
    .waitFor();
  await page
    .locator(".testing-summary summary")
    .filter({ hasText: "Earlier draft" })
    .waitFor();
  assert.deepEqual(fixture.state.errors, []);
  console.log(
    "PASS comparison and testing summary: exact proposed/current, temporary saves, pass/fail/untested, approval restart, accept, rollback and stale assessment",
  );
} catch (e) {
  console.log(await page?.locator("body").innerText());
  for (const f of page.frames())
    console.log(f.url(), await f.locator("body").innerText());
  await mkdir("test-results/review", { recursive: true });
  await page.screenshot({ path: "test-results/review/error.png" });
  throw e;
} finally {
  await app?.close();
  await fixture.close();
  await rm(root, { recursive: true, force: true });
}
