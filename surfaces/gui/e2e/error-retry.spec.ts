// Model-layer roadmap item 1 (2026-07-22): a turn that dies on a provider error leaves a
// visible, persistent marker with a Retry affordance. Retry re-runs the failed turn with NO
// new user bubble; once the turn recovers, the button disappears (the notice is history).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("provider error shows a retriable notice; Retry re-runs without a new user message", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("please fail the turn");
  await box.press("Enter");

  await expect(page.getByText("Error: model unreachable").first()).toBeVisible({ timeout: 10_000 });
  const retry = page.getByTestId("notice-retry");
  await expect(retry).toBeVisible();

  await retry.click();
  await expect(page.getByText("Recovered after retry.").first()).toBeVisible({ timeout: 10_000 });

  // No fake user bubble from the retry turn, exactly one real one…
  await expect(page.locator(".bubble-user")).toHaveCount(1);
  // …and the button is gone now that the error notice is no longer the transcript tail.
  await expect(page.getByTestId("notice-retry")).toHaveCount(0);
});
