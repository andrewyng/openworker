// Search command palette (#282): must stay viewport-centered even when opened from the
// collapsed/peeked sidebar, whose CSS transform would otherwise become the containing block
// for a nested `position: fixed` overlay.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function ready(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(page.locator(".app")).not.toHaveClass(/boot-splash/);
  await expect(page.locator(".sidebar")).toBeVisible();
}

test("search from expanded sidebar is centered in the viewport", async ({ page }) => {
  await ready(page);
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
  await ready(page);
  const app = page.locator(".app");

  await page.getByRole("button", { name: "Collapse sidebar" }).click();
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
  await ready(page);
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.locator(".app")).toHaveClass(/nav-collapsed/);

  const cluster = page.getByTestId("topbar-cluster");
  await expect(cluster).toBeVisible();
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
