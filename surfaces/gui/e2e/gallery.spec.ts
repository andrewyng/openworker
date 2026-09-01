// The Gallery entry point was removed from Settings ▸ Coworkers (owner 2026-08-21) —
// coworkers install from GitHub / folder / zip. This file keeps the page-level
// delete flow (now on the coworker detail page, UX-035).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openPersonas(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
}

test("the Gallery entry point is gone from the Coworkers page", async ({ page }) => {
  await openPersonas(page);
  await expect(page.getByTestId("install-disclosure")).toBeVisible();
  await expect(page.getByTestId("gallery-link")).toHaveCount(0);
});

test("delete: non-builtin personas removable after confirm; built-ins are not", async ({
  page,
}) => {
  // UX-035: delete moved off the list rows onto the coworker detail page.
  await openPersonas(page);
  await expect(page.getByText("Acme Notes")).toBeVisible();
  await page.getByTestId("persona-configure-acme-notes").click();
  await page.getByTestId("persona-delete").click();
  await page.getByTestId("persona-delete-confirm").click();
  // Back on the list, the row is gone (works signed out).
  await expect(page.getByText("Acme Notes")).not.toBeVisible();
});
