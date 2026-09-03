// Token-usage chip (OPE-42): after a turn reports usage, a quiet meter+count chip appears
// in the composer's bottom row; clicking it opens the per-model breakdown popover with the
// context-window fill. The fake agent attaches fixed usage to every echo turn
// (input 1k / output 200 / cache_read 8k / cache_write 800 — 10k per turn), and the
// settings fixture maps the default model to a 200k context window.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("usage chip appears after a turn and opens the breakdown popover", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // Fresh session: no usage yet — the chip is hidden entirely.
  await expect(page.getByTestId("usage-chip")).toHaveCount(0);

  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible({
    timeout: 10_000,
  });

  // Default: no bar — the chip states the in-context size (prompt side of the last
  // turn: 1k + 8k + 800 = 9.8k). Session totals are on release hold (owner call
  // 2026-08-24): context-window figures only, everywhere.
  const chip = page.getByTestId("usage-chip");
  await expect(chip).toContainText("9.8k");

  // Popover: context fill only (9.8k prompt-side of 200k = 5%) — no totals breakdown.
  await chip.click();
  const pop = page.getByTestId("usage-popover");
  await expect(pop).toBeVisible();
  await expect(pop).toContainText("Context window");
  await expect(pop).toContainText("9.8k of 200k · 5%");
  await expect(pop).not.toContainText("Session totals");
  await expect(pop).not.toContainText("Uncached input");
  await expect(pop).not.toContainText("tokens");

  // Context is a level, not a sum — a second identical turn leaves the chip unchanged.
  // The scrim click closes the popover.
  await page.mouse.click(10, 10);
  await expect(pop).toHaveCount(0);
  await box.fill("again");
  await box.press("Enter");
  await expect(page.getByText("Echo: again", { exact: false }).first()).toBeVisible({
    timeout: 10_000,
  });
  await expect(chip).toContainText("9.8k");
});

test("usage resets on a new session", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  await expect(page.getByTestId("usage-chip")).toBeVisible({ timeout: 10_000 });

  // "＋ New session" wipes the transcript — and the usage accumulation with it.
  await page.getByRole("button", { name: /New session/ }).first().click();
  await expect(page.getByTestId("usage-chip")).toHaveCount(0);
});

test("Settings toggle turns the context bar on; default is the in-context number", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  const chip = page.getByTestId("usage-chip");
  await expect(chip).toContainText("9.8k", { timeout: 10_000 }); // default: in-context size, no bar

  // Turn the bar ON in Settings -> General.
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByTestId("context-bar-toggle")).not.toBeChecked();
  const [req] = await Promise.all([
    page.waitForRequest(
      (r) => r.url().endsWith("/v1/settings/context-bar") && r.method() === "POST",
    ),
    page.getByTestId("context-bar-toggle").check(),
  ]);
  expect(req.postDataJSON()).toEqual({ context_bar: true });

  // Reload so the app re-reads settings: the chip is now the fill bar, not a number.
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByPlaceholder(/Ask the coworker/).press("Enter");
  const bar = page.getByTestId("usage-chip");
  await expect(bar).toBeVisible({ timeout: 10_000 });
  await expect(bar).not.toContainText("9.8k");
  await expect(bar).toHaveAttribute("title", /Context window 5% full/);
});
