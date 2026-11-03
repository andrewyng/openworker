import { expect, sendSessionEvent, test } from "./fixtures";

// Exercise the real App handlers with a recorded Tauri bridge. This checks drag
// requests, not the native Windows move loop (which still needs a manual check).
test("topbar controls do not request a native window drag", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__OCW_PLATFORM__ = "windows";
    (window as any).__windowDragRequests = 0;
    (window as any).__TAURI__ = {
      core: {
        invoke: async (command: string) => {
          if (command === "start_window_drag") (window as any).__windowDragRequests++;
          return null;
        },
      },
    };
  });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await sendSessionEvent(page, {
    type: "ready",
    data: { sandbox: { state: "sandboxed", provider: "openshell", network: "allowlist", sites: [] } },
  });

  const dragRequests = () => page.evaluate(() => (window as any).__windowDragRequests);
  const chip = page.getByTestId("sandbox-chip");
  await expect(chip).toBeVisible();
  await chip.locator("svg").click();
  await expect(page.getByTestId("sandbox-chip-panel")).toBeVisible();
  expect(await dragRequests()).toBe(0);
  await chip.click();
  await expect(page.getByTestId("sandbox-chip-panel")).toHaveCount(0);

  await page.getByRole("button", { name: "Hide side panel", exact: true }).click();
  await expect(page.locator(".right-rail")).toHaveCount(0);
  expect(await dragRequests()).toBe(0);
  await page.locator(".topbar-artifacts-btn").click();
  await expect(page.locator(".right-rail")).toBeVisible();
  expect(await dragRequests()).toBe(0);

  await chip.click();
  await page.getByTestId("sandbox-chip-settings").click();
  await expect(page.getByTestId("sandbox-chip-panel")).toHaveCount(0);
  expect(await dragRequests()).toBe(0);
});

test("empty title-bar space still requests a drag only for the primary button", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__windowDragRequests = 0;
    (window as any).__TAURI__ = {
      core: { invoke: async (command: string) => {
        if (command === "start_window_drag") (window as any).__windowDragRequests++;
        return null;
      } },
    };
  });
  await page.goto("/");
  const title = page.locator(".main-title");
  await expect(title).toBeVisible();
  await title.dispatchEvent("pointerdown", { button: 2, bubbles: true });
  expect(await page.evaluate(() => (window as any).__windowDragRequests)).toBe(0);
  await title.dispatchEvent("pointerdown", { button: 0, bubbles: true });
  expect(await page.evaluate(() => (window as any).__windowDragRequests)).toBe(1);
});
