import { test, expect } from "./fixtures";

test("Traditional Chinese switches immediately, persists, and preserves Simplified Chinese", async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("openworker.lang")) localStorage.setItem("openworker.lang", "en");
  });
  await page.goto("/#/settings/general");
  await page.getByRole("radio", { name: "繁體中文（台灣）", exact: true }).click();
  await expect(page.getByRole("heading", { name: "一般", exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: "繁體中文（台灣）", exact: true })).toBeChecked();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-TW");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("openworker.lang"))).toBe("zh-TW");
  await page.reload();
  await expect(page.getByRole("heading", { name: "一般", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/zh-tw-settings.png", fullPage: true });
  await page.getByRole("radio", { name: "简体中文", exact: true }).click();
  await expect(page.getByRole("heading", { name: "通用", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.getByRole("radio", { name: "English", exact: true }).click();
  await expect(page.getByRole("heading", { name: "General", exact: true })).toBeVisible();
});

test.describe("Traditional Chinese system locale", () => {
  test.use({ locale: "zh-TW" });
  test("follows the system without writing an explicit preference", async ({ page }) => {
    await page.goto("/#/settings/general");
    await expect(page.getByRole("heading", { name: "一般", exact: true })).toBeVisible();
    await expect(page.getByRole("radio", { name: "跟隨系統", exact: true })).toBeChecked();
    expect(await page.evaluate(() => localStorage.getItem("openworker.lang"))).toBeNull();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-TW");
  });
});
