// The plan dock: the current task's plan rides the composer — collapsed it names the
// live step and count, expanded it is the step card; the transcript stays untouched.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a filed plan docks to the composer and expands into the step card", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("plan-dock")).toHaveCount(0); // no plan, no chrome

  await page.getByPlaceholder(/Ask the coworker/).fill("make a plan");
  await page.getByRole("button", { name: "Send" }).click();

  const head = page.getByTestId("plan-dock-toggle");
  await expect(head).toContainText("Check tests and git history"); // the live step
  await expect(head).toContainText("2 of 4");

  await head.click();
  const steps = page.getByTestId("plan-dock-steps");
  await expect(steps.locator(".plandock-step")).toHaveCount(4);
  await expect(steps.locator(".plandock-step.done").first()).toContainText("Read README");
});

test("a finished plan stays as the Last-plan receipt until the ✕ clears it", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("finish the plan");
  await page.getByRole("button", { name: "Send" }).click();

  const head = page.getByTestId("plan-dock-toggle");
  await expect(head).toContainText("Last plan");
  await expect(head).toContainText("All steps done");
  await expect(head).toContainText("4 of 4");

  await page.getByTestId("plan-dock-clear").click();
  await expect(page.getByTestId("plan-dock")).toHaveCount(0);
});
