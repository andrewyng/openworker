import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { unzipSync } from "fflate";
const root = await mkdtemp(join(tmpdir(), "skybound-ui-")),
  evidence = resolve("test-results/skybound");
await mkdir(evidence, { recursive: true });
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
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page
    .getByRole("heading", { name: "Moonlight Rally", level: 1 })
    .waitFor();
  await page.screenshot({ path: join(evidence, "home.png") });
  await page
    .getByRole("button", { name: "Play starter Skybound Carrier", exact: true })
    .click();
  const frame = page.frameLocator("#starter-preview");
  await frame
    .getByRole("button", { name: "Begin delivery →", exact: true })
    .waitFor();
  await page.screenshot({ path: join(evidence, "title.png") });
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 700),
  );
  await page.screenshot({ path: join(evidence, "title-compact.png") });
  await frame.locator("#quality").selectOption("low");
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1390, 900),
  );
  await frame.locator("#quality").selectOption("balanced");
  const content = page
    .frames()
    .find((f) => f.url().includes("starter-skybound-carrier"));
  console.log("Initial", await content.evaluate(() => window.skyboundStatus()));
  await frame
    .getByRole("button", { name: "Begin delivery →", exact: true })
    .click();
  await frame.getByRole("button", { name: "Jump", exact: true }).click();
  assert.ok((await content.evaluate(() => window.skyboundStatus())).jump > 0);
  await page.waitForTimeout(1000);
  await frame.getByRole("button", { name: "Slide", exact: true }).click();
  assert.ok((await content.evaluate(() => window.skyboundStatus())).slide > 0);
  await frame
    .getByRole("button", { name: "Enable sound", exact: true })
    .click();
  await frame
    .getByRole("button", { name: "Mute sound", exact: true })
    .waitFor();
  await page.screenshot({ path: join(evidence, "playing.png") });
  await frame.getByRole("button", { name: "Pause game", exact: true }).click();
  let paused = await content.evaluate(() => window.skyboundStatus().distance);
  await page.waitForTimeout(250);
  assert.equal(
    await content.evaluate(() => window.skyboundStatus().distance),
    paused,
  );
  await frame
    .getByRole("button", { name: "Continue delivery →", exact: true })
    .click();
  const from = await content.evaluate(() => window.skyboundStatus().frames),
    time = Date.now();
  while (Date.now() - time < 90000) {
    const s = await content.evaluate(() => window.skyboundStatus());
    if (s.phase !== "running") break;
    const nearest = s.upcoming[0];
    if (nearest) {
      const blocked = s.upcoming
          .filter((o) => o.z === nearest.z)
          .map((o) => o.lane),
        target = [0, 1, 2].find((n) => !blocked.includes(n));
      for (let i = 0; i < Math.abs(s.lane - target); i++)
        await frame
          .getByRole("button", {
            name: target < s.lane ? "Move left" : "Move right",
            exact: true,
          })
          .click();
    }
    await page.waitForTimeout(180);
  }
  const finish = await content.evaluate(() => window.skyboundStatus());
  console.log(
    "Finished",
    finish,
    "observed animation frames/sec",
    (finish.frames - from) / ((Date.now() - time) / 1000),
  );
  assert.equal(finish.phase, "won");
  await page.screenshot({ path: join(evidence, "delivered.png") });
  await frame.getByRole("button", { name: "Run again →", exact: true }).click();
  assert.ok(
    (await content.evaluate(() => window.skyboundStatus())).distance < 20,
  );
  await page
    .getByRole("button", { name: "Make it yours", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Skybound Carrier remix", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: /assets\/courier.glb/ }).click();
  await page
    .locator(".source-view pre")
    .filter({ hasText: "Binary asset" })
    .waitFor();
  const snap = await page.evaluate(() =>
      window.arcade.action({ type: "snapshot" }),
    ),
    game = snap.games[0];
  await app.evaluate(({ dialog }, path) => {
    dialog.showOpenDialog = async () => ({
      canceled: false,
      filePaths: [path],
    });
  }, resolve("starter-games/skybound-carrier/assets/collect.wav"));
  await page
    .getByRole("button", { name: "Import assets", exact: true })
    .click();
  await page
    .locator(".source-view pre")
    .filter({ hasText: "assets/imported/collect.wav" })
    .waitFor();
  const staged = JSON.parse(
    await readFile(join(game.directory, "draft.json"), "utf8"),
  );
  assert.equal(staged.files["assets/imported/collect.wav"].encoding, "base64");
  assert.equal(
    (await page.evaluate(() => window.arcade.action({ type: "snapshot" })))
      .games[0].revisionId,
    game.revisionId,
  );
  await page.screenshot({ path: join(evidence, "imported-asset.png") });
  const dest = join(root, "skybound.zip");
  await app.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path });
  }, dest);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  let zip;
  for (let n = 0; n < 50; n++) {
    try {
      zip = await readFile(dest);
      break;
    } catch {
      await page.waitForTimeout(100);
    }
  }
  const files = unzipSync(zip);
  assert.deepEqual(
    Buffer.from(files["assets/courier.glb"]),
    await readFile("starter-games/skybound-carrier/assets/courier.glb"),
  );
  assert.equal(files["assets/imported/collect.wav"], undefined);
  assert.deepEqual(
    Buffer.from(files["assets/sky.png"]),
    await readFile("starter-games/skybound-carrier/assets/sky.png"),
  );
  assert.deepEqual(errors, []);
  console.log(
    "PASS Skybound GLB/PNG/WAV loading, gameplay delivery, controls, pause, restart, remix and byte-exact ZIP.",
  );
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
