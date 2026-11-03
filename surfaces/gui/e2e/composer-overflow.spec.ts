import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("running composer keeps Stop within a narrow session column", async ({ page }) => {
  await page.setViewportSize({ width: 1_100, height: 800 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("stream the epic");
  await box.press("Enter");

  const [composer, stop] = await Promise.all([
    page.locator(".composer").boundingBox(),
    page.getByRole("button", { name: /Stop/ }).boundingBox(),
  ]);

  expect(composer).not.toBeNull();
  expect(stop).not.toBeNull();
  expect(stop!.x + stop!.width).toBeLessThanOrEqual(composer!.x + composer!.width);
});
