import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { verifyAssetExport } from "./asset-export-helper.mjs";
const root = await mkdtemp("/tmp/arcade-asset-ui-"),
  evidence = resolve("test-results/asset-pilot");
await mkdir(evidence, { recursive: true });
const app = await electron.launch({
  ...(process.env.ARCADE_PACKAGED_APP
    ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
    : { args: [resolve(".")] }),
  env: { ...process.env, ARCADE_DATA_DIR: root },
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
  await page.getByRole("button", { name: "Play now", exact: true }).click();
  await page.frameLocator("#starter-preview").locator("#start").waitFor();
  const frame = page
    .frames()
    .find((f) => f.url().includes("starter-moonlight"));
  await frame.waitForFunction(
    () => window.rallyStatus?.().vehicleLoad === "ready",
  );
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).vehicle,
    "retro-coupe",
  );
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).wheelAnimation.wheels,
    4,
  );
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).wheelAnimation.textured,
    true,
  );
  for (const course of ["neon", "sunset"]) {
    await frame.locator(`[data-course=${course}]`).click();
    await page.waitForTimeout(1000);
    const hidden = await frame.addStyleTag({
      content: "#menu,.shade{visibility:hidden!important}",
    });
    await page.screenshot({ path: join(evidence, `car-${course}.png`) });
    await hidden.evaluate((e) => e.remove());
  }
  await frame.locator("#quality").selectOption("low");
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).vehicle,
    "classic-coupe",
  );
  await frame.locator("#quality").selectOption("balanced");
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).vehicle,
    "retro-coupe",
  );
  await frame.locator("#start").click();
  await frame.waitForFunction(() => window.rallyStatus().phase === "racing");
  const go = await frame
    .getByRole("button", { name: "Accelerate", exact: true })
    .boundingBox();
  await page.mouse.move(go.x + go.width / 2, go.y + go.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(1500);
  await page.mouse.up();
  const pointer = await frame.evaluate(() => window.rallyStatus());
  console.log("Pointer acceleration", pointer.phase, pointer.speed);
  assert.ok(pointer.speed > 10);
  await page.keyboard.down("ArrowUp");
  await page.waitForTimeout(700);
  const first = await frame.evaluate(() => window.rallyStatus());
  await page.waitForTimeout(300);
  const second = await frame.evaluate(() => window.rallyStatus());
  await page.keyboard.up("ArrowUp");
  assert.notEqual(
    first.wheelAnimation.rotation,
    second.wheelAnimation.rotation,
  );
  await frame.locator("#pause").click();
  const frozen = (await frame.evaluate(() => window.rallyStatus()))
    .wheelAnimation.rotation;
  await page.waitForTimeout(200);
  assert.equal(
    (await frame.evaluate(() => window.rallyStatus())).wheelAnimation.rotation,
    frozen,
  );
  // Failure injection is limited to this test document's model fetch; production code stays unchanged.
  await page.addInitScript(() => {
    const fetch = window.fetch;
    window.fetch = function (input, ...args) {
      const url =
        typeof input === "string" ? input : (input?.url ?? String(input));
      if (url.includes("retro-coupe.glb"))
        return Promise.reject(new Error("Simulated missing model"));
      return fetch.call(this, input, ...args);
    };
  });
  await page.reload();
  await page.getByRole("button", { name: "Play now", exact: true }).click();
  await page.frameLocator("#starter-preview").locator("#start").waitFor();
  const fallback = page
    .frames()
    .find((f) => f.url().includes("starter-moonlight"));
  await fallback.waitForFunction(
    () => window.rallyStatus?.().vehicleLoad === "fallback",
  );
  assert.equal(
    (await fallback.evaluate(() => window.rallyStatus())).vehicle,
    "classic-coupe",
  );
  assert.match(
    await fallback.locator("#vehicle-status").textContent(),
    /unavailable/,
  );
  await fallback.locator("#start").click();
  await fallback.waitForFunction(() => window.rallyStatus().phase === "racing");
  await verifyAssetExport(page, app, root, "retro-coupe");
  assert.deepEqual(errors, []);
  assert.deepEqual(remote, []);
  console.log(
    "PASS detailed car loads offline, wheels animate/freeze, low quality and missing-asset fallback remain playable",
  );
} finally {
  await app.close();
  await rm(root, { recursive: true, force: true });
}
