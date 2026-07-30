// #311: switching away from a mid-turn session used to leave `running` stuck false on
// return (turn_start already fired). ready.running must restore Stop / live chrome.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("switching back to a mid-turn session restores the Stop control", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // Park on an approval — the turn stays live (no turn_done) so ready.running stays true.
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("run a tool");
  await box.press("Enter");
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/wants to run a command/i)).toBeVisible();

  // Leave for another session while the turn is still live.
  await page.getByText("Weekly plan 1").first().click();
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);

  // Return — ready.running seeds the live chrome even though turn_start won't re-fire.
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible({ timeout: 10_000 });
});
