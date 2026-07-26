// Issue #95: on macOS the window has no native title bar while onboarding is on screen
// (App.tsx's `overlay`, gated to `platformOS() === "macos"` — hidden_title(true) in
// src-tauri/src/lib.rs), so the ONLY way to move the window is a `data-tauri-drag-region`
// element. Onboarding's full-viewport backdrop (z-50) sits over both of the app's drag
// surfaces (Sidebar's brand row, App.tsx's topbar), and carries no drag region of its own,
// so the window is stuck until onboarding finishes.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openOnboarding(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Run setup again" }).click();
  await expect(page.getByTestId("ob-step-model")).toBeVisible();
}

test("macOS: a drag region is reachable while onboarding covers the screen", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__TAURI__ = {};
    (window as any).__OCW_PLATFORM__ = "macos";
  });
  await openOnboarding(page);

  // The overlay title bar is macOS-only; confirm it actually engaged for this run.
  await expect(page.locator(".app.tauri-overlay").first()).toBeVisible();

  const dragRegions = page.locator("[data-tauri-drag-region]");
  await expect(dragRegions.first()).toBeVisible();

  // The mechanism Tauri relies on: the element the OS would hit-test at the drag
  // region's own coordinates must BE that element, not something stacked on top of it.
  const reachable = await dragRegions.first().evaluate((el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + Math.min(10, r.width / 2);
    const y = r.top + r.height / 2;
    return document.elementFromPoint(x, y) === el;
  });
  expect(reachable).toBe(true);
});
