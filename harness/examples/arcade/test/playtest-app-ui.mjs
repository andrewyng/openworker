import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, rm, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { modelFixture, until } from "./model-fixture.mjs";
const root = await mkdtemp(join(tmpdir(), "arcade-playtest-app-")),
  fixture = await modelFixture();
fixture.state.mode = "preview";
const app = await electron.launch({
  ...(process.env.ARCADE_PACKAGED_APP
    ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
    : { args: [resolve(".")] }),
  env: {
    ...process.env,
    ARCADE_DATA_DIR: root,
    OPENWORKER_RUNTIME:
      process.env.OPENWORKER_RUNTIME ??
      resolve(
        `../../target/release/openworker-harness${process.platform === "win32" ? ".exe" : ""}`,
      ),
  },
  timeout: 60000,
});
try {
  const page = await app.firstWindow();
  page.setDefaultTimeout(20000);
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
    .fill("Inspect the initial draft without publishing it.");
  await page
    .getByRole("button", { name: "Build my game", exact: true })
    .click();
  await page
    .getByText("Preview inspection completed; publication was not requested.", {
      exact: true,
    })
    .waitFor();
  const images = fixture.state.requests.flatMap((r) =>
    r.messages.flatMap((m) =>
      Array.isArray(m.content)
        ? m.content.filter((c) => c.type === "image_url")
        : [],
    ),
  );
  assert.ok(
    images.length >= 2,
    "packaged runtime must hydrate actual preview images",
  );
  assert.ok(
    images.every((i) =>
      i.image_url.url.startsWith("data:image/jpeg;base64,/9j/"),
    ),
  );
  assert.equal(
    await page.getByRole("button", { name: "Allow once", exact: true }).count(),
    0,
  );
  await until(
    async () =>
      await app.evaluate(
        ({ BrowserWindow }) => BrowserWindow.getAllWindows().length === 1,
      ),
    "playtest window cleaned up",
  );
  await mkdir("test-results/playtest-app", { recursive: true });
  await page.screenshot({
    path: join("test-results/playtest-app", "completed.png"),
  });
  assert.deepEqual(fixture.state.errors, []);
  console.log(
    "PASS app playtest: real UI -> real SDK -> isolated renderer -> image-bearing model requests, no publication and renderer cleanup",
  );
} finally {
  await app.close();
  await fixture.close();
  await rm(root, { recursive: true, force: true });
}
