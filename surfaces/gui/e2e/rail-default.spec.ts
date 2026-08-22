// UX-038 follow-up (owner ruling 2026-08-21): the right rail starts hidden and the
// topbar toggle's choice survives a restart. Deep links (artifact chips) force-show
// transiently without overwriting the stored preference.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function clearRailPref(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.evaluate(() => {
    localStorage.setItem("ocw-e2e-rail-default", "1"); // opt out of the fixture seed
    localStorage.removeItem("coworker:rail-hidden:v1");
  });
  await page.reload();
}

test("rail is hidden by default; the toggle persists across restarts", async ({ page }) => {
  await clearRailPref(page);
  await expect(page.getByTestId("rail-toggle-artifacts")).toHaveCount(0);

  // Show it — the choice must survive a reload ("restart").
  await page.getByRole("button", { name: "Show side panel" }).click();
  await expect(page.getByTestId("rail-toggle-artifacts")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("rail-toggle-artifacts")).toBeVisible();

  // Hide it — that persists too.
  await page.getByRole("button", { name: "Hide side panel" }).click();
  await page.reload();
  await expect(page.getByTestId("rail-toggle-artifacts")).toHaveCount(0);
});

test("an artifact chip force-shows the rail without overwriting the hidden preference", async ({ page }) => {
  await clearRailPref(page);
  // "show the report" makes the fixture echo carry an [artifact:] chip.
  await page.getByPlaceholder(/Ask the coworker/).fill("show the report");
  await page.getByRole("button", { name: "Send" }).click();

  // The transcript's artifact chip opens the viewer even though the rail is hidden.
  await page.getByTestId("artifact-chip").click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();

  // The stored preference is untouched: a reload starts hidden again.
  await page.reload();
  await expect(page.getByTestId("rail-toggle-artifacts")).toHaveCount(0);
});
