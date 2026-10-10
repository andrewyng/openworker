import { test, expect } from "./fixtures";

async function newDraftAs(page: import("@playwright/test").Page, coworker: RegExp) {
  await page.getByText("New session").first().click();
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByRole("button", { name: coworker }).click();
}

// Models per coworker (connectors-across-machines spec §4): a coworker's `models:` list
// restricts the composer picker to the list, marks entries this machine cannot run, and
// binds a fresh draft to the first runnable entry. A coworker without a list keeps the
// machine's full selectable list.
test("a coworker's models list restricts the picker and binds the draft", async ({ page }) => {
  await page.goto("/");
  await newDraftAs(page, /Security Coworker/);

  // Bound to the first runnable entry of the list.
  const picker = page.locator(".dd").filter({ hasText: "Claude Opus 4.8" });
  await expect(picker).toBeVisible();
  await picker.locator(".pill").click();
  const items = page.locator(".dd-item");
  await expect(items).toHaveCount(2);
  await expect(items.filter({ hasText: "qwen3-coder:30b" })).toContainText(
    "Not available on this machine",
  );
  // The machine's other models (GPT-5.5 is on /v1/settings) are not offered here.
  await expect(items.filter({ hasText: "GPT-5.5" })).toHaveCount(0);
  await page.locator(".dd-backdrop").click();
  // (A coworker without a list keeps the machine's full list — model-switch.spec covers it.)
});
