// #611 — "Hide thinking while running": a persisted Composer setting that collapses the
// fast-scrolling live reasoning trace to a quiet one-line "Thinking…" indicator while a
// turn runs, WITHOUT hiding the fact that work is ongoing. The finalized reasoning still
// appears in the assistant item's "Thought process" disclosure after the turn.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function startThinkingTurn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("think hard about this");
  await box.press("Enter");
}

test("hide-reasoning: default shows the live thinking block", async ({ page }) => {
  await startThinkingTurn(page);

  // OFF by default: the live ThinkingBlock streams the trace (existing behavior).
  await expect(page.getByText("Thinking…").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("thinking-toggle")).toBeVisible();
  await page.getByTestId("thinking-toggle").click();
  await expect(page.getByTestId("thinking-body")).toContainText("Weighing options.");
});

test("hide-reasoning: collapses live thinking but keeps an ongoing-work indicator", async ({
  page,
}) => {
  // Enable the setting in Settings → Composer (General tab is the default open tab).
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const togg = page.getByTestId("hide-reasoning-toggle");
  await expect(togg).toBeVisible();
  await togg.check();
  await expect(togg).toBeChecked();

  // Run a reasoning turn: the live ThinkingBlock must be suppressed…
  await startThinkingTurn(page);
  await expect(page.getByText("Thinking…").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("thinking-toggle")).toHaveCount(0);

  // …but the user still sees that work is ongoing (the quiet indicator, not raw text).
  // No streaming trace is shown while hidden.
  await expect(page.getByTestId("thinking-body")).toHaveCount(0);

  // The finalized reasoning still lands after the turn — nothing was lost.
  await expect(page.getByText("Decision made.").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Thinking…")).toHaveCount(0);
  const disc = page.getByTestId("thinking-toggle");
  await expect(disc).toHaveText(/Thought process/);
  await disc.click();
  await expect(page.getByTestId("thinking-body")).toContainText(
    "Weighing options. Comparing tradeoffs. Settling it.",
  );
});
