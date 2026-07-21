import { test, expect } from "./fixtures";

// Sidebar session lifecycle (owner testing pass, 2026-07-03): the peek cap (sessions_peek=5 →
// "Show more (2)" with 7 sessions), reversible archive with the Archived disclosure, and the
// two-step delete (Delete arms → "Delete?" confirms). All row actions sit behind the per-row
// ⋮ kebab (FB-011), so each flow goes hover → kebab → menu item.

test("session list caps at the peek count with Show more", async ({ page }) => {
  await page.goto("/");
  // Boot resumes a cowork session, so the Coworker accordion body is expanded.
  await expect(page.getByTitle("Weekly plan 1")).toBeVisible();
  await expect(page.getByTitle("Weekly plan 5")).toBeVisible();
  await expect(page.getByTitle("Weekly plan 6")).toHaveCount(0);

  await page.getByRole("button", { name: "Show more (2)" }).click();
  await expect(page.getByTitle("Weekly plan 6")).toBeVisible();
  await expect(page.getByTitle("Weekly plan 7")).toBeVisible();
});

test("archive via the row menu is reversible via the Archived disclosure", async ({ page }) => {
  await page.goto("/");
  const row = page.getByTitle("Weekly plan 2");
  await expect(row).toBeVisible();

  await row.hover();
  await row.getByTestId("row-menu").click();
  await row.getByTestId("row-menu-archive").click();

  // Gone from the main list; parked under the Archived disclosure.
  await expect(page.getByTitle("Weekly plan 2")).toHaveCount(0);
  await page.getByRole("button", { name: /Archived \(1\)/ }).click();
  const archivedRow = page.getByTitle("Weekly plan 2");
  await expect(archivedRow).toBeVisible();

  // Unarchive (same menu slot on an archived row) brings it straight back; the disclosure
  // disappears with its last item.
  await archivedRow.hover();
  await archivedRow.getByTestId("row-menu").click();
  await expect(archivedRow.getByTestId("row-menu-archive")).toHaveText("Unarchive");
  await archivedRow.getByTestId("row-menu-archive").click();
  await expect(page.getByRole("button", { name: /Archived/ })).toHaveCount(0);
  await expect(page.getByTitle("Weekly plan 2")).toBeVisible();
});

test("mention-spawned sessions collapse under From Slack with the platform icon (§31)", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByTitle("Weekly plan 1")).toBeVisible();

  // Collapsed by default with a count; the session row hidden until expanded…
  const toggle = page.getByTestId("from-slack-toggle");
  await expect(toggle).toContainText("From Slack (1)");
  await expect(page.getByTitle("#general — check the deploy?")).toHaveCount(0);

  await toggle.click();
  const row = page.getByTitle("#general — check the deploy?");
  await expect(row).toBeVisible();
  // …wearing the Slack logo (hover-hidden cluster, so assert attachment not visibility)…
  await expect(
    page.getByTestId("from-slack-list").locator('[data-logo="slack"]'),
  ).toHaveCount(1);
  // …and never duplicated into any other list.
  await expect(page.getByTitle("#general — check the deploy?")).toHaveCount(1);
});

test("pin via the row menu moves the session to the Pinned band and back", async ({ page }) => {
  await page.goto("/");
  const row = page.getByTitle("Weekly plan 4");
  await expect(row).toBeVisible();

  await row.hover();
  await row.getByTestId("row-menu").click();
  await expect(row.getByTestId("row-menu-pin")).toHaveText("Pin");
  await row.getByTestId("row-menu-pin").click();

  // Pinned rows live ONLY in the cross-persona Pinned band — no duplicate in the body.
  const pinnedBand = page.getByText("Pinned", { exact: true }).locator("..");
  await expect(pinnedBand.getByTitle("Weekly plan 4")).toBeVisible();
  await expect(page.getByTitle("Weekly plan 4")).toHaveCount(1);

  const pinnedRow = pinnedBand.getByTitle("Weekly plan 4");
  await pinnedRow.hover();
  await pinnedRow.getByTestId("row-menu").click();
  await expect(pinnedRow.getByTestId("row-menu-pin")).toHaveText("Unpin");
  await pinnedRow.getByTestId("row-menu-pin").click();
  await expect(pinnedBand.getByTitle("Weekly plan 4")).toHaveCount(0);
  await expect(page.getByTitle("Weekly plan 4")).toHaveCount(1);
});

test("delete is two-step: the menu's Delete arms, Delete? confirms", async ({ page }) => {
  await page.goto("/");
  const row = page.getByTitle("Weekly plan 3");
  await expect(row).toBeVisible();

  await row.hover();
  await row.getByTestId("row-menu").click();
  await row.getByTestId("row-menu-delete").click();
  // First click only ARMS — the menu stays open showing the confirm affordance, the row remains.
  await expect(row.getByTestId("row-menu-delete")).toHaveText("Delete?");
  await expect(page.getByTitle("Weekly plan 3")).toHaveCount(1);

  await row.getByTestId("row-menu-delete").click();
  await expect(page.getByTitle("Weekly plan 3")).toHaveCount(0);
});
