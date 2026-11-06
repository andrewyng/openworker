import { _electron as electron } from "playwright";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import assert from "node:assert/strict";
const evidence = resolve("test-results/live"),
  result = JSON.parse(await readFile(join(evidence, "result.json"), "utf8"));
const app = await electron.launch({
  ...(process.env.ARCADE_PACKAGED_APP
    ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
    : { args: [resolve(".")] }),
  env: { ...process.env, ARCADE_DATA_DIR: result.root },
  timeout: 60000,
});
try {
  const page = await app.firstWindow();
  await page
    .getByRole("button", { name: "Play Golden Catch", exact: true })
    .click();
  const frame = page.frameLocator("#game-preview");
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await frame.getByRole("button", { name: "Play", exact: true }).click();
  await frame.locator("#overlay").waitFor({ state: "hidden" });
  await frame.locator("canvas").click();
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(evidence, "golden-catch.png") });
  await frame.getByRole("button", { name: "Restart", exact: true }).click();
  assert.equal(await frame.locator("#score").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log(
    "PASS: live Opus multi-file game played and restarted in the actual app",
  );
} finally {
  await app.close();
}
