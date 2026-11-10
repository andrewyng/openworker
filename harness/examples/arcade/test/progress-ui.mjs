import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { once } from "node:events";
const root = await mkdtemp(join(tmpdir(), "arcade-progress-ui-"));
await mkdir("test-results/progress", { recursive: true });
let app, page, frame, content;
async function launch() {
  app = await electron.launch({
    ...(process.env.ARCADE_PACKAGED_APP
      ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
      : { args: [resolve(".")] }),
    env: { ...process.env, ARCADE_DATA_DIR: root },
    timeout: 60000,
  });
  page = await app.firstWindow({ timeout: 20000 });
  page.setDefaultTimeout(20000);
  await page.getByRole("button", { name: "Play now", exact: true }).click();
  frame = page.frameLocator("#starter-preview");
  await frame.locator("#start").waitFor();
  content = page
    .frames()
    .find((f) => f.url().includes("starter-moonlight-rally"));
  await content.waitForFunction(
    () => !document.querySelector("#start").disabled,
  );
}
try {
  await launch();
  await frame.locator("#start").click();
  await content.waitForFunction(() => window.rallyStatus().phase === "racing");
  await page.keyboard.down("ArrowUp");
  await page.waitForTimeout(2200);
  await page.keyboard.up("ArrowUp");
  await frame.getByRole("button", { name: "Pause race", exact: true }).click();
  await frame
    .locator("#save-status")
    .filter({ hasText: "Progress saved" })
    .waitFor();
  // Read the acknowledged record, not the last render frame, as crash recovery's contract.
  const saved = await content.evaluate(() =>
    window.arcadeSave.load("race-neon", 2),
  );
  assert.ok(saved.value.car.progress > 5);
  await content.evaluate(async () => {
    await window.arcadeSave.submitScore("test-best", 12, "lower");
    await window.arcadeSave.submitScore("test-best", 8, "lower");
    await window.arcadeSave.submitScore("test-best", 10, "lower");
  });
  // Crash only this test's isolated Electron process, after a write was acknowledged.
  const child = app.process(),
    exited = once(child, "exit");
  child.kill("SIGKILL");
  await exited;
  app = null;
  await launch();
  await frame.locator("#continue").click();
  const restored = await content.evaluate(() => window.rallyStatus());
  assert.equal(restored.phase, "paused");
  assert.equal(restored.time, saved.value.car.time);
  assert.equal(restored.progress, saved.value.car.progress);
  await page.waitForTimeout(150);
  assert.equal(
    (await content.evaluate(() => window.rallyStatus())).time,
    restored.time,
  );
  assert.equal(
    (await content.evaluate(() => window.arcadeSave.best("test-best"))).score,
    8,
  );
  await page.screenshot({ path: "test-results/progress/restored.png" });
  await frame
    .getByRole("button", { name: "Resume race →", exact: true })
    .click();
  await page.waitForTimeout(200);
  assert.ok(
    (await content.evaluate(() => window.rallyStatus())).time > restored.time,
  );
  assert.equal(await content.evaluate(() => typeof window.arcade), "undefined");
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Play starter Lumen Garden", exact: true })
    .click();
  const other =
    page.frames().find((f) => f.url().includes("starter-lumen-garden")) ??
    (await (
      await page.locator("#starter-preview").elementHandle()
    ).contentFrame());
  await other.waitForFunction(() => !!window.arcadeSave);
  assert.equal(
    await other.evaluate(() => window.arcadeSave.load("race-neon", 2)),
    null,
  );
  assert.equal(
    await other.evaluate(() => window.arcadeSave.best("test-best")),
    null,
  );
  await other.evaluate(() =>
    parent.postMessage(
      {
        type: "arcade:progress",
        url: "arcade-preview://starter-moonlight-rally.v1-1-0/index.html",
        request: { op: "remove", slot: "race-neon" },
      },
      "*",
    ),
  );
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page.getByRole("button", { name: "Play now", exact: true }).click();
  frame = page.frameLocator("#starter-preview");
  await frame.locator("#continue").waitFor();
  await page
    .getByRole("button", { name: "Make it yours", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Moonlight Rally remix", exact: true })
    .waitFor();
  const remix = await (
    await page.locator("#game-preview").elementHandle()
  ).contentFrame();
  await remix.waitForFunction(() => !!window.arcadeSave);
  assert.equal(
    await remix.evaluate(() => window.arcadeSave.load("race-neon", 2)),
    null,
  );
  assert.equal(
    await remix.evaluate(() => window.arcadeSave.best("test-best")),
    null,
  );
  // Version mismatch remains intact until the player explicitly chooses New race.
  const legacy = structuredClone(saved.value);
  delete legacy.rules;
  delete legacy.car.raceLaps;
  delete legacy.car.lapTimes;
  delete legacy.car.lapStartTime;
  await remix.evaluate(
    (value) => window.arcadeSave.save("lap-neon", 1, value),
    legacy,
  );
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  const legacyFrame = page.frameLocator("#game-preview");
  await legacyFrame
    .getByRole("button", { name: "Continue saved 1-lap race →", exact: true })
    .click();
  const legacyContent = await (
    await page.locator("#game-preview").elementHandle()
  ).contentFrame();
  assert.equal(
    (await legacyContent.evaluate(() => window.rallyStatus())).raceLaps,
    1,
  );
  assert.equal(
    (await legacyContent.evaluate(() => window.rallyStatus())).phase,
    "paused",
  );
  await legacyContent.evaluate(() =>
    window.arcadeSave.save("race-neon", 3, { future: true }),
  );
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  const rf = page.frameLocator("#game-preview");
  await rf
    .locator("#save-status")
    .filter({ hasText: "INCOMPATIBLE_SAVE" })
    .waitFor();
  const reloaded = await (
    await page.locator("#game-preview").elementHandle()
  ).contentFrame();
  assert.deepEqual(
    (await reloaded.evaluate(() => window.arcadeSave.load("race-neon", 3)))
      .value,
    { future: true },
  );
  await rf.locator("#start").click();
  await reloaded.waitForFunction(() => window.rallyStatus().phase === "racing");
  assert.equal(
    await reloaded.evaluate(() => window.arcadeSave.load("race-neon", 2)),
    null,
  );
  console.log(
    "PASS acknowledged checkpoint across forced process death, paused Continue, best scores, cross-game/forged-message isolation, fresh remix, incompatible-save preservation and explicit reset",
  );
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
