// Search command palette (#282): must stay viewport-centered even when opened from the
// collapsed/peeked sidebar, whose CSS transform would otherwise become the containing block
// for a nested `position: fixed` overlay.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("search from expanded sidebar is centered in the viewport", async ({ page }) => {
  await page.goto("/");
  await page.locator(".sidebar").getByRole("button", { name: "Search", exact: true }).click();

  const panel = page.getByTestId("search-modal-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByPlaceholder("Search chats")).toBeVisible();

  const box = await panel.boundingBox();
  const viewport = page.viewportSize();
  expect(box).toBeTruthy();
  expect(viewport).toBeTruthy();
  const panelCenter = box!.x + box!.width / 2;
  const viewportCenter = viewport!.width / 2;
  expect(Math.abs(panelCenter - viewportCenter)).toBeLessThan(8);
});

test("search from peeked collapsed sidebar stays viewport-centered", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");

  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);

  // Hover the left-edge zone to peek the floating sidebar, then open Search from it.
  await page.locator(".nav-hover-zone").hover();
  await expect(app).toHaveClass(/nav-peek/);
  await page.locator(".sidebar").getByRole("button", { name: "Search", exact: true }).click();

  const panel = page.getByTestId("search-modal-panel");
  await expect(panel).toBeVisible();

  // Peek should dismiss so the floating sidebar does not cover the palette.
  await expect(app).not.toHaveClass(/nav-peek/);

  const box = await panel.boundingBox();
  const viewport = page.viewportSize();
  expect(box).toBeTruthy();
  expect(viewport).toBeTruthy();
  const panelCenter = box!.x + box!.width / 2;
  const viewportCenter = viewport!.width / 2;
  expect(Math.abs(panelCenter - viewportCenter)).toBeLessThan(8);
});

test("collapsed topbar search is also viewport-centered", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Meta+b");

  const cluster = page.getByTestId("topbar-cluster");
  await cluster.getByRole("button", { name: "Search" }).click();

  const panel = page.getByTestId("search-modal-panel");
  await expect(panel).toBeVisible();

  const box = await panel.boundingBox();
  const viewport = page.viewportSize();
  expect(box).toBeTruthy();
  expect(viewport).toBeTruthy();
  const panelCenter = box!.x + box!.width / 2;
  const viewportCenter = viewport!.width / 2;
  expect(Math.abs(panelCenter - viewportCenter)).toBeLessThan(8);
});
