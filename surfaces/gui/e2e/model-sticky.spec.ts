// A composer model pick is STICKY (2026-07-24): it also becomes the default for new
// sessions. Before this, the pick was session-scoped only — every "New session" snapped
// back to whatever configuring the provider had stamped as the default, so anyone running
// a custom model had to re-pick on each conversation.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a composer model pick becomes the default for new sessions", async ({ page }) => {
  await page.goto("/");

  // The model chip — an uncurated id renders raw ("gpt-5.5"), a curated one its label.
  const chip = page.locator(".dd").filter({ hasText: /claude opus 4\.8|gpt-5\.5/i });
  await expect(chip).toContainText("Claude Opus 4.8");

  await chip.locator(".pill").click();
  await page.locator(".dd-item").filter({ hasText: "gpt-5.5" }).click();
  await expect(chip).toContainText("gpt-5.5");

  // New session: the server binds the persisted default, so the pick rides along.
  await page.getByRole("button", { name: "New session" }).click();
  await expect(chip).toContainText("gpt-5.5");

  // And it outlives the app — a cold boot reads the same default back from the server.
  await page.reload();
  await expect(chip).toContainText("gpt-5.5");
});
