import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { once } from "node:events";
import { verifyAssetExport } from "./asset-export-helper.mjs";
const root = await mkdtemp("/tmp/pip-adventure-ui-"),
  evidence = resolve("test-results/adventure");
await mkdir(evidence, { recursive: true });
let app, page, f, fl;
const errors = [],
  remote = [];
async function launch() {
  app = await electron.launch({
    ...(process.env.ARCADE_PACKAGED_APP
      ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
      : { args: [resolve(".")] }),
    env: { ...process.env, ARCADE_DATA_DIR: root },
    timeout: 60000,
  });
  page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (/^https?:/.test(r.url())) remote.push(r.url());
  });
  await page
    .getByRole("button", { name: "Play starter Pip's Adventure", exact: true })
    .click();
  fl = page.frameLocator("#starter-preview");
  await fl.locator("#start").waitFor({ state: "attached" });
  f = page.frames().find((f) => f.url().includes("starter-pip-garden"));
  await f.waitForFunction(() => window.pipStatus?.().assetLoaded);
  await fl
    .locator("#panel-copy")
    .filter({ hasText: /Find the missing|saved journey|checkpoint could/ })
    .waitFor();
}
const status = () => f.evaluate(() => window.pipStatus());
async function go(x, z) {
  let held = new Set(),
    reached = false;
  const start = Date.now();
  while (Date.now() - start < 20000) {
    const s = await status();
    if (s.phase === "paused") {
      await fl.locator("#start").click();
      held.clear();
    }
    if (Math.hypot(x - s.x, z - s.z) < 0.25) {
      reached = true;
      break;
    }
    const next = new Set();
    if (Math.abs(x - s.x) > 0.11)
      next.add(x < s.x ? "ArrowLeft" : "ArrowRight");
    if (Math.abs(z - s.z) > 0.11) next.add(z < s.z ? "ArrowUp" : "ArrowDown");
    for (const k of held) if (!next.has(k)) await page.keyboard.up(k);
    for (const k of next) if (!held.has(k)) await page.keyboard.down(k);
    held = next;
    await page.waitForTimeout(40);
  }
  for (const k of held) await page.keyboard.up(k);
  assert.ok(reached, `walk to ${x},${z}: ${JSON.stringify(await status())}`);
}
try {
  await launch();
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1390, 900),
  );
  await page.screenshot({ path: join(evidence, "home-title.png") });
  await fl.locator("#start").click();
  await f.waitForFunction(() => window.pipStatus().phase === "playing");
  await page.keyboard.press("Space");
  await f.waitForFunction(() => window.pipStatus().y > 0.1);
  await f.waitForFunction(() => window.pipStatus().y === 0);
  await fl.locator("#pause").click();
  const paused = await status();
  await page.waitForTimeout(250);
  assert.equal((await status()).mixerTime, paused.mixerTime);
  await fl.locator("#start").click();
  for (const [x, z] of [
    [-11, 12],
    [10, 11],
    [-7, -3],
  ])
    await go(x, z);
  assert.deepEqual((await status()).parts, [true, true, true]);
  await go(0, -4);
  await go(0, -8);
  await page.keyboard.press("KeyE");
  await f.waitForFunction(() => window.pipStatus().bridge);
  await page.waitForTimeout(1900);
  await go(0, -22);
  await fl.locator("#pause").click();
  const hide = await f.addStyleTag({
    content: "#panel{visibility:hidden!important}",
  });
  await page.screenshot({ path: join(evidence, "bridge.png") });
  if (process.env.PIP_CAPTURE_ART === "1") {
    const hiddenUI = await f.addStyleTag({content:'header,#panel,#objective,#map-panel,.touch,#toast,#interaction{visibility:hidden!important}'});
    await fl.locator('#game').screenshot({path:resolve('starter-games/pip-garden/assets/adventure-cover.png')});
    await hiddenUI.evaluate(el=>el.remove());
  }
  await hide.evaluate((e) => e.remove());
  await fl.locator("#start").click();
  await go(0, -35);
  await f.waitForFunction(() => window.pipStatus().checkpoint === "woodland");
  await fl.locator("#pause").click();
  await f.waitForFunction(async () => {
    const s = await window.arcadeSave.load("adventure", 1);
    return s?.value.checkpoint === "woodland";
  });
  const acknowledged = await f.evaluate(() =>
    window.arcadeSave.load("adventure", 1),
  );
  const child = app.process(),
    dead = once(child, "exit");
  child.kill("SIGKILL");
  await dead;
  app = null;
  await launch();
  assert.equal(await fl.locator("#start").textContent(), "Continue adventure");
  await fl.locator("#start").click();
  assert.equal((await status()).phase, "paused");
  assert.equal((await status()).checkpoint, "woodland");
  assert.deepEqual((await status()).parts, [true, true, true]);
  assert.equal((await status()).x, acknowledged.value.x);
  await page.screenshot({ path: join(evidence, "restored.png") });
  await fl.locator("#start").click();
  for (const [x, z, target, index] of [
    [-10, -43, 1, 0],
    [9, -51, 3, 1],
    [-6, -63, 0, 2],
  ]) {
    await go(x, z + 1.8);
    for (let i = 0; i < 4 && (await status()).dials[index] !== target; i++) {
      await page.keyboard.press("KeyE");
      await page.waitForTimeout(120);
    }
    assert.equal((await status()).dials[index], target);
  }
  await fl.locator("#pause").click();
  const hidden = await f.addStyleTag({
    content: "#panel{visibility:hidden!important}",
  });
  await page.screenshot({ path: join(evidence, "woodland.png") });
  await hidden.evaluate((e) => e.remove());
  await fl.locator("#start").click();
  await go(0, -67.2);
  await page.keyboard.press("KeyE");
  await f.waitForFunction(() => window.pipStatus().completed);
  assert.equal((await status()).clip, "Dance");
  await page.waitForTimeout(2400);
  await page.screenshot({ path: join(evidence, "complete.png") });
  await fl.locator("#start").click();
  assert.equal((await status()).phase, "playing");
  await go(0, -65);
  await fl.locator("#pause").click();
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 700),
  );
  assert.ok(
    await fl.locator("#panel").evaluate((el) => {
      const b = el.getBoundingClientRect();
      return (
        b.left >= 0 &&
        b.right <= innerWidth &&
        b.top >= 0 &&
        b.bottom <= innerHeight
      );
    }),
  );
  assert.equal(
    await fl.locator("#objective").evaluate((el) => getComputedStyle(el).visibility),
    "hidden",
    "pause menu hides the objective card instead of overlapping it",
  );
  await page.screenshot({ path: join(evidence, "compact.png") });
  await fl.locator("#quality").selectOption("low");
  await fl.locator("#quality").selectOption("balanced");
  await fl.locator("#new").click();
  await fl.locator("#cancel").click();
  assert.ok((await status()).completed);
  const complete = await f.evaluate(() =>
    window.arcadeSave.load("adventure", 1),
  );
  assert.ok(complete.value.completed);
  await verifyAssetExport(page, app, root, "pip-robot");
  // A fresh remix has no checkpoint from the bundled original.
  await page.getByRole("button", { name: "Play", exact: true }).click();
  const remix = page.frameLocator("#game-preview");
  await remix
    .locator("#start")
    .filter({ hasText: "Begin adventure" })
    .waitFor();
  let rf = page
    .frames()
    .find((f) => f.url().includes("arcade-preview://game-"));
  assert.equal(
    await rf.evaluate(() => window.arcadeSave.load("adventure", 1)),
    null,
  );
  await rf.evaluate(() =>
    window.arcadeSave.save("adventure", 2, { future: true }),
  );
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await remix
    .locator("#panel-copy")
    .filter({ hasText: "checkpoint could not be read" })
    .waitFor();
  rf = page.frames().find((f) => f.url().includes("arcade-preview://game-"));
  assert.equal(
    (await rf.evaluate(() => window.arcadeSave.load("adventure", 2))).value
      .future,
    true,
  );
  await remix.locator("#new").click();
  await remix.locator("#cancel").click();
  assert.equal(
    (await rf.evaluate(() => window.arcadeSave.load("adventure", 2))).value
      .future,
    true,
  );
  await remix.locator("#new").click();
  await remix.locator("#start").click();
  await rf.waitForFunction(
    () => window.pipStatus().saveState === "Saved on this device",
  );
  assert.deepEqual(
    (await rf.evaluate(() => window.arcadeSave.load("adventure", 1))).value
      .parts,
    [false, false, false],
  );
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = function (input, ...rest) {
      if (String(input?.url ?? input).includes("pip-robot.glb"))
        return Promise.reject(Error("Missing model test"));
      return original.call(this, input, ...rest);
    };
  });
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await remix
    .locator("#panel-title")
    .filter({ hasText: "garden could not load" })
    .waitFor();
  assert.deepEqual(errors, []);
  assert.deepEqual(remote, []);
  console.log(
    "PASS adventure: two areas, bridge/lantern puzzle, jump/pause, acknowledged crash recovery, compact controls, cancellation of reset, future-save preservation, explicit reset, missing-asset error, fresh remix and licensed export",
  );
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
