import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { unzipSync } from "fflate";
import {
  buildTrack,
  atDistance,
  angleDelta,
} from "../starter-games/moonlight-rally/physics.js";
const root = await mkdtemp(join(tmpdir(), "rally-ui-")),
  evidence = resolve("test-results/rally");
await mkdir(evidence, { recursive: true });
const exportedNames = [
  "game.js",
  "vehicle.js",
  "physics.js",
  "progress.js",
  "art.js",
  "courses/neon.json",
  "courses/sunset.json",
  "LICENSE.txt",
  "assets/neon-cover.png",
  "assets/sunset-cover.png",
];
const expectedExports = new Map(
  await Promise.all(
    exportedNames.map(async (name) => [
      name,
      await readFile(`starter-games/moonlight-rally/${name}`),
    ]),
  ),
);
expectedExports.set(
  "assets/library/retro-coupe.glb",
  await readFile("asset-library/retro-coupe.glb"),
);
let app;
try {
  app = await electron.launch({
    ...(process.env.ARCADE_PACKAGED_APP
      ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
      : { args: [resolve(".")] }),
    env: { ...process.env, ARCADE_DATA_DIR: root },
    timeout: 60000,
  });
  const page = await app.firstWindow({ timeout: 20000 });
  page.setDefaultTimeout(20000);
  const errors = [],
    remote = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("request", (r) => {
    if (/^https?:/.test(r.url())) remote.push(r.url());
  });
  await page
    .getByRole("heading", { name: "Moonlight Rally", exact: true, level: 1 })
    .waitFor();
  await page.getByRole("button", { name: "Play now", exact: true }).click();
  const frame = page.frameLocator("#starter-preview");
  await frame
    .getByRole("button", { name: "Start race →", exact: true })
    .waitFor();
  const content = page
    .frames()
    .find((f) => f.url().includes("starter-moonlight-rally"));
  await content.waitForFunction(
    () => window.rallyStatus?.().vehicleLoad === "ready",
  );
  assert.equal(
    (await content.evaluate(() => window.rallyStatus())).wheelAnimation
      .textured,
    true,
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1390, 900),
  );
  await page.screenshot({ path: join(evidence, "neon-title.png") });
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 700),
  );
  await page.screenshot({ path: join(evidence, "compact.png") });
  await frame.locator("#quality").selectOption("low");
  await frame.locator("#quality").selectOption("balanced");
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1390, 900),
  );
  for (const id of ["neon", "sunset"]) {
    await content.evaluate(
      (id) => window.arcadeSave.submitScore(`race-${id}-3lap-v2`, 600, "lower"),
      id,
    );
    await frame.locator(`[data-course="${id}"]`).click();
    await frame.locator("#best").filter({ hasText: "10:00.00" }).waitFor();
    await page.screenshot({ path: join(evidence, `${id}-menu.png`) });
    const track = buildTrack(
      JSON.parse(
        await readFile(
          `starter-games/moonlight-rally/courses/${id}.json`,
          "utf8",
        ),
      ),
    );
    await frame.locator("#start").click();
    await content.waitForFunction(
      () => window.rallyStatus().phase === "racing",
    );
    await frame
      .getByRole("button", { name: "Pause race", exact: true })
      .click();
    const frozen = await content.evaluate(() => window.rallyStatus().time);
    await page.waitForTimeout(200);
    assert.equal(
      await content.evaluate(() => window.rallyStatus().time),
      frozen,
    );
    await frame
      .getByRole("button", { name: "Resume race →", exact: true })
      .click();
    const start = Date.now(),
      initial = await content.evaluate(() => window.rallyStatus());
    let held = new Set(),
      focusPauses = 0,
      coastStart = null,
      lastLap = 0,
      captured = false;
    // Real keyboard events against the rendered game; never mutate production state.
    while (Date.now() - start < 300000) {
      const s = await content.evaluate(() => window.rallyStatus());
      if (s.phase === "finished") break;
      if (s.lapTimes.length > lastLap) {
        lastLap = s.lapTimes.length;
        console.log(id, "completed lap", lastLap, s.time);
      }
      if (s.phase === "coasting") {
        for (const k of held) await page.keyboard.up(k);
        held.clear();
        if (!coastStart) {
          coastStart = s;
          await page.screenshot({ path: join(evidence, `${id}-coasting.png`) });
        }
        assert.equal(s.time, coastStart.time);
        await page.waitForTimeout(80);
        continue;
      }
      if (s.phase === "paused" && focusPauses++ < 5) {
        for (const k of held) await page.keyboard.up(k);
        held.clear();
        await app.evaluate(({ BrowserWindow }) =>
          BrowserWindow.getAllWindows()[0].focus(),
        );
        await frame
          .getByRole("button", { name: "Resume race →", exact: true })
          .click();
        continue;
      }
      assert.equal(s.phase, "racing");
      const q = atDistance(track, s.progress + 8 + Math.abs(s.speed) * 0.3),
        error = angleDelta(Math.atan2(q.x - s.x, -(q.z - s.z)), s.yaw);
      const curve = Math.abs(
          angleDelta(atDistance(track, s.progress + 25).yaw, s.yaw),
        ),
        target = curve > 0.6 ? 12 : curve > 0.3 ? 18 : 27;
      const next = new Set();
      if (s.speed < target) next.add("ArrowUp");
      if (s.speed > target + 2) next.add("ArrowDown");
      if (error > 0.035) next.add("ArrowRight");
      if (error < -0.035) next.add("ArrowLeft");
      for (const k of held) if (!next.has(k)) await page.keyboard.up(k);
      for (const k of next) if (!held.has(k)) await page.keyboard.down(k);
      held = next;
      if (!captured && s.progress > track.length * 0.2) {
        // Capturing large images can take seconds: pause rather than hold a turn
        // without feedback while the driver is blocked on screenshot encoding.
        for (const k of held) await page.keyboard.up(k);
        held.clear();
        await frame
          .getByRole("button", { name: "Pause race", exact: true })
          .click();
        const hideMenu = await content.addStyleTag({
          content: "#menu{visibility:hidden!important}",
        });
        await page.screenshot({ path: join(evidence, `${id}-driving.png`) });
        const hiddenUI = await content.addStyleTag({
          content:
            "header,#menu,.shade,#toast,#telemetry,.touch,#reset,#countdown {visibility:hidden!important}",
        });
        await frame
          .locator("#world")
          .screenshot({ path: join(evidence, `${id}-canvas.png`) });
        if (process.env.RALLY_CAPTURE_ART === "1")
          await frame.locator("#world").screenshot({
            path: resolve(
              `starter-games/moonlight-rally/assets/${id}-cover.png`,
            ),
          });
        await hiddenUI.evaluate((node) => node.remove());
        await hideMenu.evaluate((node) => node.remove());
        await frame
          .getByRole("button", { name: "Resume race →", exact: true })
          .click();
        captured = true;
      }
      await page.waitForTimeout(65);
    }
    for (const k of held) await page.keyboard.up(k);
    const final = await content.evaluate(() => window.rallyStatus());
    console.log(
      id,
      final,
      "observed FPS",
      (final.frames - initial.frames) / ((Date.now() - start) / 1000),
    );
    assert.equal(final.phase, "finished");
    await frame
      .locator("#result-detail")
      .filter({ hasText: "New personal best" })
      .waitFor();
    assert.equal(final.checkpoint, 36);
    assert.equal(final.lapTimes.length, 3);
    assert.ok(coastStart);
    assert.ok(Math.hypot(final.x - coastStart.x, final.z - coastStart.z) > 10);
    assert.equal(final.time, coastStart.time);
    await frame.locator("#best").filter({ hasText: "Personal best" }).waitFor();
    assert.equal(
      (
        await content.evaluate(
          (id) => window.arcadeSave.best(`race-${id}-3lap-v2`),
          id,
        )
      ).score,
      final.time,
    );
    assert.equal(
      await content.evaluate(
        (id) => window.arcadeSave.load(`race-${id}`, 2),
        id,
      ),
      null,
    );
    assert.ok(final.hits < 15, "reasonable controllability");
    await content.waitForFunction(
      () =>
        getComputedStyle(document.querySelector(".menu-copy")).opacity === "1",
    );
    await page.screenshot({ path: join(evidence, `${id}-finish.png`) });
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(900, 700),
    );
    await page.screenshot({ path: join(evidence, `${id}-finish-compact.png`) });
    assert.equal(
      await frame.locator(".menu-copy").evaluate((el) => {
        const r = el.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
      }),
      true,
      "results fit the compact game viewport",
    );
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(1390, 900),
    );
  }
  // GPU resources should stabilize after switching back and forth, not accumulate.
  const resources = [];
  for (const id of ["neon", "sunset", "neon", "sunset", "neon"]) {
    await frame.locator(`[data-course="${id}"]`).click();
    await page.waitForTimeout(200);
    if (id === "neon")
      resources.push(await content.evaluate(() => window.rallyStatus()));
  }
  assert.ok(resources.at(-1).geometries <= resources[0].geometries + 2);
  assert.ok(resources.at(-1).textures <= resources[0].textures + 2);
  await frame
    .getByRole("button", { name: "Enable sound", exact: true })
    .click();
  await frame
    .getByRole("button", { name: "Mute sound", exact: true })
    .waitFor();
  await frame.locator("#start").click();
  await content.waitForFunction(() => window.rallyStatus().phase === "racing");
  // Hold/release a pointer control, then exercise keyboard handbrake and reset.
  const go = await frame
    .getByRole("button", { name: "Accelerate", exact: true })
    .boundingBox();
  await page.mouse.move(go.x + go.width / 2, go.y + go.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(1500);
  await page.mouse.up();
  const pointerState = await content.evaluate(() => window.rallyStatus());
  assert.ok(
    pointerState.speed > 10,
    `pointer acceleration: ${JSON.stringify(pointerState)}`,
  );
  await page.keyboard.down("ArrowRight");
  await page.keyboard.down("Space");
  await page.waitForTimeout(250);
  assert.ok((await content.evaluate(() => window.rallyStatus())).drift > 0.08);
  if (
    !(await content.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ))
  )
    assert.ok(
      (await content.evaluate(() => window.rallyStatus())).smokeActive > 0,
    );
  assert.ok((await content.evaluate(() => window.rallyStatus())).skidGain > 0);
  await page.keyboard.up("Space");
  await page.keyboard.up("ArrowRight");
  const beforeReset = await content.evaluate(() => window.rallyStatus());
  await frame.locator("#reset").click();
  const afterReset = await content.evaluate(() => window.rallyStatus());
  assert.equal(afterReset.speed, 0);
  assert.ok(afterReset.time >= beforeReset.time);
  assert.equal(afterReset.checkpoint, beforeReset.checkpoint);
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .focus();
  assert.equal(
    (await content.evaluate(() => window.rallyStatus())).phase,
    "paused",
    "leaving game focus pauses driving",
  );
  await page
    .getByRole("button", { name: "Make it yours", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Moonlight Rally remix", exact: true })
    .waitFor();
  const snap = await page.evaluate(() =>
      window.arcade.action({ type: "snapshot" }),
    ),
    game = snap.games[0];
  assert.ok(game.revisionId);
  const dest = join(root, "rally.zip");
  await app.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path });
  }, dest);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  let zip;
  for (let i = 0; i < 50; i++) {
    try {
      zip = await readFile(dest);
      break;
    } catch {
      await page.waitForTimeout(100);
    }
  }
  const files = unzipSync(zip);
  for (const [file, expected] of expectedExports)
    assert.deepEqual(Buffer.from(files[file]), expected);
  assert.match(
    Buffer.from(files["assets/library/retro-coupe.LICENSE.txt"]).toString(),
    /CC0/,
  );
  assert.ok(files["vendor/three.module.js"]);
  assert.deepEqual(errors, []);
  assert.deepEqual(remote, []);
  console.log(
    "PASS both three-lap races, timed coast-down, medal/best comparisons, responsive results, smoke/audio, stable GPU resources, remix and export",
  );
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
