import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import { readFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { verifyAssetExport } from "./asset-export-helper.mjs";
const bundled = process.env.PIP_BUNDLED === "1";
if (bundled) {
  await import("./adventure-ui.mjs");
  process.exit(0);
}
const evidence = resolve("test-results/asset-pilot");
await mkdir(evidence, { recursive: true });
const result = bundled
  ? { root: await mkdtemp("/tmp/arcade-pip-bundled-") }
  : JSON.parse(await readFile(join(evidence, "live.json"), "utf8"));
const app = await electron.launch({
  ...(process.env.ARCADE_PACKAGED_APP
    ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
    : { args: [resolve(".")] }),
  env: { ...process.env, ARCADE_DATA_DIR: result.root },
  timeout: 60000,
});
try {
  const page = await app.firstWindow(),
    errors = [],
    remote = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (/^https?:/.test(r.url())) remote.push(r.url());
  });
  await page
    .getByRole("heading", { name: "Moonlight Rally", level: 1 })
    .waitFor();
  if (bundled)
    await page
      .getByRole("button", { name: "Play starter Pip's Garden", exact: true })
      .click();
  else {
    await page.getByRole("button", { name: /^My games/ }).click();
    await page
      .getByRole("button", { name: "Play Pip's Garden", exact: true })
      .click();
  }
  const frameLocator = page.frameLocator(
    bundled ? "#starter-preview" : "#game-preview",
  );
  await frameLocator.locator("#start").waitFor();
  const f = page.frames().find((f) => f.url().startsWith("arcade-preview:"));
  await f.waitForFunction(
    () => window.pipStatus?.().assetLoaded,
    {},
    { timeout: 30000 },
  );
  assert.equal((await f.evaluate(() => window.pipStatus())).clip, "Idle");
  await page.screenshot({ path: join(evidence, "pip-title.png") });
  await frameLocator
    .getByRole("button", { name: "Start", exact: true })
    .click();
  await page.keyboard.down("ArrowUp");
  await f.waitForFunction(() => window.pipStatus().clip === "Running");
  assert.equal((await f.evaluate(() => window.pipStatus())).clip, "Running");
  await page.keyboard.up("ArrowUp");
  await f.waitForFunction(() => window.pipStatus().clip === "Idle");
  assert.equal((await f.evaluate(() => window.pipStatus())).clip, "Idle");
  await page.keyboard.press("Space");
  await page.waitForTimeout(100);
  assert.equal((await f.evaluate(() => window.pipStatus())).clip, "Jump");
  await f.waitForFunction(() => window.pipStatus().clip === "Idle");
  await frameLocator
    .getByRole("button", { name: "Pause", exact: true })
    .click();
  const paused = await f.evaluate(() => window.pipStatus());
  await page.waitForTimeout(250);
  assert.equal(
    (await f.evaluate(() => window.pipStatus())).mixerTime,
    paused.mixerTime,
  );
  await frameLocator
    .getByRole("button", { name: "Resume", exact: true })
    .first()
    .click();
  // Drive with keyboard only. Waypoints skirt the garden's visible trees/boulders.
  let held = new Set();
  for (const [x, z] of [
    [-2.5, -1.5],
    [0, -3],
    [3.8, -1.8],
    [-2.5, -4.8],
    [-6.2, -4.2],
    [-3.5, -6.8],
    [-1.2, -8],
    [0, 0],
    [2.5, 5.8],
  ]) {
    const start = Date.now();
    let reached = false;
    while (Date.now() - start < 12000) {
      const s = await f.evaluate(() => window.pipStatus());
      if (s.phase === "won") {
        reached = true;
        break;
      }
      if (s.phase === "paused")
        await frameLocator
          .getByRole("button", { name: "Resume", exact: true })
          .first()
          .click();
      const dx = x - s.x,
        dz = z - s.z;
      if (Math.hypot(dx, dz) < 0.28) {
        reached = true;
        break;
      }
      const next = new Set();
      if (Math.abs(dx) > 0.12) next.add(dx < 0 ? "ArrowLeft" : "ArrowRight");
      if (Math.abs(dz) > 0.12) next.add(dz < 0 ? "ArrowUp" : "ArrowDown");
      for (const k of held) if (!next.has(k)) await page.keyboard.up(k);
      for (const k of next) if (!held.has(k)) await page.keyboard.down(k);
      held = next;
      await page.waitForTimeout(45);
    }
    for (const k of held) await page.keyboard.up(k);
    held.clear();
    assert.ok(
      reached,
      `reachable waypoint ${x},${z}: ${JSON.stringify(await f.evaluate(() => window.pipStatus()))}`,
    );
  }
  const won = await f.evaluate(() => window.pipStatus());
  assert.equal(won.phase, "won");
  assert.equal(won.score, 5);
  assert.equal(won.clip, "Dance");
  await page.screenshot({ path: join(evidence, "pip-win.png") });
  await frameLocator
    .getByRole("button", { name: "Restart", exact: true })
    .click();
  assert.equal((await f.evaluate(() => window.pipStatus())).score, 0);
  await page.keyboard.down("ArrowDown");
  await page.waitForTimeout(200);
  await page.keyboard.up("ArrowDown");
  await frameLocator
    .getByRole("button", { name: "Pause", exact: true })
    .click();
  const hide = await f.addStyleTag({
    content: "#panel,header,.touch,footer{visibility:hidden!important}",
  });
  await frameLocator
    .locator("#game")
    .screenshot({ path: join(evidence, "pip-cover.png") });
  await hide.evaluate((e) => e.remove());
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 700),
  );
  await page.screenshot({ path: join(evidence, "pip-compact.png") });
  if (bundled) await verifyAssetExport(page, app, result.root, "pip-robot");
  assert.deepEqual(errors, []);
  assert.deepEqual(remote, []);
  console.log(
    "PASS Pip: actual GLB, idle/run/jump/dance clips, five collected fireflies, mixer pause, restart and offline gameplay",
    won,
  );
} finally {
  await app.close();
  if (bundled) await rm(result.root, { recursive: true, force: true });
}
