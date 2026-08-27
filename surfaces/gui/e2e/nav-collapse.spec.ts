// Left-nav polish (§20): collapse (⌘B / brand button → reveal button docks it back) and the
// RECENT-header group/filter popover (Group by Persona↔Chronological, Filter by coworker).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("collapse hides the sidebar and reclaims the width; reveal button docks it back", async ({
  page,
}) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();

  // Collapse via the brand button.
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(app).toHaveClass(/nav-collapsed/);
  // The floating reveal affordance appears; clicking it docks the nav back.
  const reveal = page.getByRole("button", { name: "Show sidebar" });
  await expect(reveal).toBeVisible();
  await reveal.click();
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("⌘B toggles the sidebar collapse", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);
  await page.keyboard.press("Meta+b");
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("reveal button stays clickable on non-session screens", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");

  // Settings uses the standalone reveal button instead of the session topbar cluster.
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByText("Settings", { exact: true }).click();
  await page.getByRole("button", { name: "Collapse sidebar" }).click();

  const reveal = page.getByRole("button", { name: "Show sidebar" });
  await expect(reveal).toBeVisible();
  await reveal.hover();

  // Hovering the explicit control must not replace its own click target with a transient peek.
  await expect(reveal).toBeVisible();
  await expect(app).not.toHaveClass(/nav-peek/);
  await reveal.click();
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("left edge still previews the collapsed sidebar", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);

  // The zone is intentionally transparent and may sit below other fixed chrome; moving the
  // pointer to its exposed left-edge coordinates exercises the real interaction directly.
  await page.mouse.move(1, 100);
  await expect(app).toHaveClass(/nav-peek/);
  await expect(page.locator(".sidebar")).toBeVisible();
});

test("RECENT header group/filter popover: switch grouping + see coworker filters", async ({
  page,
}) => {
  await page.goto("/");
  const header = page.getByTestId("recent-header");
  await expect(header).toContainText("Recent");

  await header.getByRole("button", { name: "Group and filter conversations" }).click();
  const menu = page.getByTestId("group-filter-menu");
  await expect(menu).toContainText("Group by");
  await expect(menu).toContainText("Filter by coworker");

  // Switch to Chronological → the persona accordion collapses into a flat list (the "OpenWorker"
  // persona group header is no longer a row; sessions list directly).
  await menu.getByText("Chronological").click();
  await expect(menu.getByText("Chronological").locator("xpath=..")).toContainText("✓");

  // Filter-by-coworker checkboxes are present (none checked by default → all shown).
  await expect(menu).toContainText("None checked shows all.");
});
