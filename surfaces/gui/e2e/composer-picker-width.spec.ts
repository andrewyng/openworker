import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("model picker leaves no unused space after a short label", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const picker = page.locator(".composer .dd").first();
  const button = picker.locator("button.pill").first();
  await expect(button).toBeVisible();
  const metrics = await picker.evaluate((el) => ({
    wrapper: el.getBoundingClientRect().width,
    button: el.querySelector("button.pill")!.getBoundingClientRect().width,
  }));
  expect(Math.abs(metrics.wrapper - metrics.button)).toBeLessThanOrEqual(1);
});

for (const width of [1100, 900]) {
  test(`model picker keeps send and stop inside the composer at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    await page.getByText("Draft the launch note").first().click();
    const composer = page.locator(".composer");
    // Both idle and streaming states share the same control row.
    const assertContained = async () => {
      const box = await composer.boundingBox();
      const action = await composer.getByRole("button", { name: /^(Send|Stop)$/ }).boundingBox();
      expect(box).not.toBeNull();
      expect(action).not.toBeNull();
      expect(action!.x).toBeGreaterThanOrEqual(box!.x);
      expect(action!.x + action!.width).toBeLessThanOrEqual(box!.x + box!.width + 1);
    };
    await assertContained();
    const input = page.getByPlaceholder(/Ask the coworker/);
    await input.fill("stream the epic");
    await input.press("Enter");
    await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
    await assertContained();
  });
}
