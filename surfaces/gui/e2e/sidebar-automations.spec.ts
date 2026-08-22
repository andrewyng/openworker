// UX-023, revised: automations get exactly ONE row of sidebar presence — the "Automations" nav
// row under Search, carrying the enabled count and the aggregate unseen-run badge. The old
// per-automation "Scheduled" band is gone: at fifteen automations it was thirty lines of
// two-line entries that pushed Recent off the screen, and every entry duplicated the Automations
// page. The badge's tooltip names the automations behind it, so "which one is unhappy?" is still
// answerable without leaving the sidebar.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the nav row carries the count and the aggregate unseen badge; no per-automation band", async ({
  page,
}) => {
  await page.goto("/");

  const nav = page.getByTestId("nav-automations");
  await expect(nav).toBeVisible();
  await expect(nav).toContainText("Automations");
  // Two enabled fixtures; two unseen runs, both on the noisy one.
  await expect(nav).toContainText("2");
  const badge = page.getByTestId("automations-unseen");
  await expect(badge).toHaveText("2");
  // The tooltip is where the per-automation detail went.
  await expect(badge).toHaveAttribute("title", /Daily AI News — 2 new, latest failed/);

  // No band, and no per-automation rows anywhere in the sidebar.
  await expect(page.getByTestId("scheduled-band")).toHaveCount(0);
  await expect(page.getByTestId("scheduled-task-1")).toHaveCount(0);

  // Runs still never appear as session rows (their sessions are __run__-prefixed and hidden).
  await expect(page.getByTitle("__run__r1")).toHaveCount(0);
});

test("opening an automation from the page marks it seen and clears the sidebar badge", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("nav-automations").click();

  // The overview lists them; opening one lands on its detail…
  await page.getByText("Daily AI News").first().click();
  await expect(page.getByRole("heading", { name: "Daily AI News" })).toBeVisible();
  // …runs newer than the pre-open seen mark wear the "new" pill…
  await expect(page.getByTestId("run-new").first()).toBeVisible();
  // …and the sidebar badge clears without waiting for the 15s poll (mark-seen broadcast).
  await expect(page.getByTestId("automations-unseen")).toHaveCount(0);
});

test("the nav row opens the Automations overview", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-automations").click();
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
});

test("deleting an automation drops the sidebar count at once; re-entry lands on the list", async ({
  page,
}) => {
  await page.goto("/");
  const nav = page.getByTestId("nav-automations");
  await expect(nav).toContainText("2");

  await nav.click();
  await page.getByText("Weekly CRM digest").first().click();
  await expect(page.getByRole("heading", { name: "Weekly CRM digest" })).toBeVisible();
  await page.getByRole("button", { name: /Delete/ }).click();

  // The count drops immediately (broadcast, not the poll).
  await expect(nav).toContainText("1");

  // After visiting a session, the nav row must land on the OVERVIEW — the remembered detail
  // target for a deleted automation once left "Loading…" forever.
  await page.getByTitle("Weekly plan 1").click();
  await nav.click();
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
});
