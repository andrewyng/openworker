import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
const root = await mkdtemp(join(tmpdir(), "arcade-discovery-"));
const evidence = resolve("test-results/discovery");
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
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page
    .getByRole("heading", { name: "Moonlight Rally", exact: true, level: 1 })
    .waitFor();
  await page
    .locator(".starter-art img")
    .evaluateAll((images) => Promise.all(images.map((i) => i.decode())));
  assert.equal(await page.locator(".sidebar").count(), 0);
  assert.equal(await page.locator(".starter-card").count(), 6);
  await page.screenshot({ path: join(evidence, "home.png") });
  // The desktop minimum size must retain usable controls and have no horizontal overflow.
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 700),
  );
  await page.screenshot({ path: join(evidence, "home-compact.png") });
  assert.equal(
    await page.locator(".featured-index").evaluate((el) => {
      const container = el.closest(".featured-game").getBoundingClientRect();
      return [...el.querySelectorAll("button")].every((b) => {
        const r = b.getBoundingClientRect();
        return (
          r.left >= container.left &&
          r.right <= container.right &&
          r.bottom <= container.bottom
        );
      });
    }),
    true,
    "all six featured-game controls fit inside the hero",
  );
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(1390, 900),
  );
  await page
    .getByRole("button", { name: "Play starter Neon Drift", exact: true })
    .click();
  let frame = page.frameLocator("#starter-preview");
  await frame.getByRole("button", { name: /ENTER THE CIRCUIT/ }).click();
  await frame.locator("#overlay").waitFor({ state: "hidden" });
  await frame.locator("#arena").focus();
  await page.keyboard.press("ArrowRight");
  await page.screenshot({ path: join(evidence, "neon-playing.png") });
  await frame.getByRole("button", { name: "Pause game", exact: true }).click();
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await frame.getByRole("button", { name: /ENTER THE CIRCUIT/ }).waitFor();
  const content = page
    .frames()
    .find((f) => f.url().startsWith("arcade-preview:"));
  assert.deepEqual(
    await content.evaluate(async () => {
      let parentBlocked = false,
        networkBlocked = false,
        crossBlocked = false;
      try {
        parent.document.body;
      } catch {
        parentBlocked = true;
      }
      try {
        await fetch("https://example.com");
      } catch {
        networkBlocked = true;
      }
      try {
        await fetch("arcade-preview://starter-lumen-garden.v1-0-0/index.html");
      } catch {
        crossBlocked = true;
      }
      return {
        parentBlocked,
        networkBlocked,
        crossBlocked,
        bridge: typeof window.arcade,
        node: typeof require,
      };
    }),
    {
      parentBlocked: true,
      networkBlocked: true,
      crossBlocked: true,
      bridge: "undefined",
      node: "undefined",
    },
  );
  await page
    .getByRole("button", { name: "Make it yours", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Neon Drift remix", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: /LICENSE.txt/ }).click();
  await page
    .locator(".source-view pre")
    .filter({ hasText: "MIT License" })
    .waitFor();
  await page.screenshot({ path: join(evidence, "remix-source.png") });
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page.getByRole("button", { name: /My games/ }).click();
  await page.getByRole("heading", { name: "My games", exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Play Neon Drift remix", exact: true })
    .waitFor();
  await page.screenshot({ path: join(evidence, "my-games.png") });
  await page.getByRole("button", { name: "Home", exact: true }).click();
  await page
    .getByRole("button", { name: "Play starter Lumen Garden", exact: true })
    .click();
  frame = page.frameLocator("#starter-preview");
  await frame.getByRole("button", { name: /Enter the garden/ }).click();
  await frame.locator("#tile-controls button").first().click();
  await page.screenshot({ path: join(evidence, "garden-playing.png") });
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  const snapshot = await page.evaluate(() =>
    window.arcade.action({ type: "snapshot" }),
  );
  assert.equal(snapshot.runtime, "idle");
  assert.equal(snapshot.games.length, 1);
  assert.equal(snapshot.games[0].session, null);
  await page
    .getByRole("button", { name: "Play starter Orbit Runner", exact: true })
    .click();
  frame = page.frameLocator("#starter-preview");
  await frame.getByRole("button", { name: /Let’s play/ }).click();
  await frame.getByRole("button", { name: "Move left", exact: true }).click();
  await page.screenshot({ path: join(evidence, "orbit-playing.png") });
  assert.deepEqual(errors, []);
  console.log(
    "Discovery verified: offline catalog, layouts, starter play, licensed remix, file isolation, no model startup.",
  );
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
