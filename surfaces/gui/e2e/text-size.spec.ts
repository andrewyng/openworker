// Text size (UX-048 follow-up): a three-step control in Settings ▸ General sets
// data-text-size on <html>, persists per device, and the token scale follows.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("Text size: Large sets the root attribute, survives reload, Default clears it", async ({ page }) => {
  await page.goto("/");
  const html = page.locator("html");
  await expect(html).not.toHaveAttribute("data-text-size", /.+/);
  const body = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--fs-body").trim());
  expect(await body()).toBe("14px");

  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByTestId("text-size-large").click();
  await expect(html).toHaveAttribute("data-text-size", "large");
  expect(await body()).toBe("15px");

  await page.reload();
  await expect(html).toHaveAttribute("data-text-size", "large");

  // Settings keeps the left column while open — the reload lands back on the settings
  // route, so the control is still on screen.
  await page.getByTestId("text-size-small").click();
  expect(await body()).toBe("13px");
  await page.getByTestId("text-size-default").click();
  await expect(html).not.toHaveAttribute("data-text-size", /.+/);
  expect(await body()).toBe("14px");
});
