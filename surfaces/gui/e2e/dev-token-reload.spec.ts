// #786: the browser dev GUI used to bake the standalone server's token into the bundle when
// vite started, so after a server restart (or a vite that started first) the signed-out
// gate's Reload resent the stale token forever. vite.config.ts now reads the token file on
// each page load, so Reload recovers.
import fs from "node:fs";
import path from "node:path";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("Reload on the signed-out gate picks up a restarted server's token", async ({ page }) => {
  const tokenFile = path.join(process.env.OPENWORKER_E2E_STATE_DIR!, "sidecar-8765.token");
  // The tab loads while the file still holds the old server's token; the server now
  // running expects the new one.
  fs.writeFileSync(tokenFile, "token-before-restart\n");
  try {
    // Registered after the fixtures' mocks, so it wins; the right token falls through to them.
    await page.route(/\/v1\/(health|settings|sessions)(\?.*)?$/, (route) =>
      route.request().headers()["x-openworker-token"] === "token-after-restart"
        ? route.fallback()
        : route.fulfill({
            status: 401,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Unauthorized" }),
          }),
    );
    await page.goto("/");
    const gate = page.getByTestId("unauthorized-gate");
    await expect(gate).toBeVisible({ timeout: 10_000 });

    // The restarted server has written its token; Reload must carry it.
    fs.writeFileSync(tokenFile, "token-after-restart\n");
    const authed = page.waitForRequest(
      (req) =>
        /\/v1\/health(\?.*)?$/.test(req.url()) &&
        req.headers()["x-openworker-token"] === "token-after-restart",
    );
    await gate.getByRole("button", { name: "Reload" }).click();
    await authed;
    await expect(gate).toBeHidden();
  } finally {
    fs.rmSync(tokenFile, { force: true });
  }
});
