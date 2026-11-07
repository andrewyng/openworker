import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { modelFixture, until } from "./model-fixture.mjs";
const root = await mkdtemp(join(tmpdir(), "arcade-ui-")),
  fixture = await modelFixture(),
  evidence = resolve("test-results/creation");
await mkdir(evidence, { recursive: true });
let app;
const launch = async () => {
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
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page
    .getByRole("heading", { name: "Moonlight Rally", exact: true, level: 1 })
    .waitFor();
  return page;
};
try {
  let page = await launch();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Ollama Local", exact: true }).click();
  await page.getByLabel("Model ID", { exact: true }).fill("fixture-game");
  await page.getByText("Connection details", { exact: true }).click();
  await page
    .getByLabel("Base URL", { exact: true })
    .fill(fixture.provider.baseUrl);
  await page
    .getByRole("button", { name: "Test connection", exact: true })
    .click();
  await page
    .getByRole("status")
    .filter({ hasText: "Connection works" })
    .waitFor();
  await page.screenshot({ path: join(evidence, "model-settings.png") });
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  const gamesRoot = join(root, "My games");
  await mkdir(gamesRoot);
  await app.evaluate(({ dialog }, path) => {
    dialog.showOpenDialog = async () => ({
      canceled: false,
      filePaths: [path],
    });
  }, gamesRoot);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Storage", exact: true }).click();
  await page
    .getByRole("button", { name: "Choose games folder", exact: true })
    .click();
  await page
    .locator(".directory-path")
    .filter({ hasText: "My games" })
    .waitFor();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page
    .getByLabel("Game idea", { exact: true })
    .fill("A delightful game catching stars, with a scoreboard and restart.");
  await page.getByLabel("Look & feel", { exact: true }).selectOption("Neon");
  await page.screenshot({ path: join(evidence, "brief.png") });
  fixture.state.delay = 2500;
  await page
    .getByRole("button", { name: "Build my game", exact: true })
    .click();
  await page.locator(".streaming").filter({ hasText: "I’m shaping" }).waitFor();
  await page.screenshot({ path: join(evidence, "streaming.png") });
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  await page.screenshot({ path: join(evidence, "approval.png") });
  // Full application exit at a durable approval: reopening must not regenerate.
  const before = fixture.state.requests.length;
  await app.close();
  app = undefined;
  page = await launch();
  await page.getByRole("button", { name: /^Continue building/ }).click();
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  assert.equal(fixture.state.requests.length, before);
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  const frame = page.frameLocator("#game-preview");
  await frame
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .click();
  await frame
    .getByRole("button", { name: "Catch a star · 1", exact: true })
    .waitFor();
  await page
    .getByText(
      "Your game is ready to play. Catch stars and try for a new high score!",
      { exact: true },
    )
    .waitFor();
  await page.screenshot({ path: join(evidence, "play.png") });
  await page.locator(".activity-toggle").click();
  await page.screenshot({ path: join(evidence, "activity.png") });
  await page.locator(".activity-toggle").click();
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await frame
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .waitFor();
  fixture.state.delay = 150;
  await page
    .getByLabel("Change your game", { exact: true })
    .fill("Add more stars");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await frame
    .getByText("Version 2 · Catch a little joy", { exact: true })
    .waitFor();
  await until(
    () => page.getByLabel("Change your game", { exact: true }).isEnabled(),
    "edit complete",
  );
  await page.getByRole("button", { name: "Versions", exact: true }).click();
  await page
    .locator(".versions-panel button")
    .filter({ hasText: "Version 1" })
    .click();
  await frame
    .getByText("Version 1 · Catch a little joy", { exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Close versions", exact: true })
    .click();
  // Replace only the OS dialog in the test process; the actual export handler runs.
  const exportPath = join(root, "export.html");
  await app.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path });
  }, exportPath);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await until(async () => {
    try {
      return (await readFile(exportPath, "utf8")).includes("Version 1");
    } catch {
      return false;
    }
  }, "exported file");
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Clone Star Catcher", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page.screenshot({ path: join(evidence, "library.png") });
  const requestCount = fixture.state.requests.length;
  await app.close();
  app = undefined;
  page = await launch();
  await page
    .getByRole("button", { name: "Play Star Catcher", exact: true })
    .click();
  await page
    .frameLocator("#game-preview")
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .waitFor();
  assert.equal(fixture.state.requests.length, requestCount);
  assert.deepEqual(fixture.state.errors, []);
  console.log(
    "PASS: model settings/probe, brief, streaming, approval across app restart, play, edit, restore, export, duplicate and offline reopen",
  );
  console.log(`Screenshots: ${evidence}`);
} catch (error) {
  if (app) {
    const page = await app.firstWindow();
    await page.screenshot({ path: join(evidence, "failure.png") });
    console.error((await page.locator("body").innerText()).slice(-6000));
  }
  console.error(
    "Fixture requests",
    fixture.state.requests.length,
    "errors",
    fixture.state.errors,
  );
  throw error;
} finally {
  await app?.close();
  await fixture.close();
  await rm(root, { recursive: true, force: true });
}
