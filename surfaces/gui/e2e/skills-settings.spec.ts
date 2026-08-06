import { test, expect } from "./fixtures";

// SKILLS-SPEC §9 journey 1 — Settings ▸ Skills as the management home: create through the
// Add-skill menu, edit in place, disable with the amber clean-slate banner, and the
// rich-skill folder chip. Hermetic: every /v1 call lands in fixtures.ts.

const openSkills = async (page: import("@playwright/test").Page) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Skills", exact: true }).click();
};

test("skills-settings: create via the menu → name-first banner; edit persists", async ({ page }) => {
  await openSkills(page);

  // The seeded rows render; the rich one wears its folder chip; the list is the page
  // (no standing add-surfaces).
  await expect(page.getByText("weekly-report")).toBeVisible();
  await expect(page.getByText("uploaded")).toBeVisible();
  await expect(page.getByTitle("Show folder")).toContainText("2 files");
  await expect(page.getByText("Start a conversation")).toHaveCount(0);

  // Add skill ▾ → the three doors, then Write it myself.
  await page.getByRole("button", { name: /Add skill/ }).click();
  await expect(page.getByText("Import a file")).toBeVisible();
  await expect(page.getByText("Create with OpenWorker")).toBeVisible();
  await page.getByText("Write it myself").click();

  await page.getByLabel("Name").fill("greet-warmly");
  await page.getByLabel("Description").fill("Greets people warmly");
  await page.getByLabel("Instructions").fill("Always greet warmly.");
  await page.getByRole("button", { name: "Save skill" }).click();

  // Name-first teal confirmation (§7) + the new row.
  const status = page.getByRole("status");
  await expect(status).toContainText("greet-warmly");
  await expect(status).toContainText("can now use it in every conversation");
  await expect(page.getByText("Greets people warmly")).toBeVisible();

  // Edit: pencil prefills, name locked, save PATCHes through to the re-fetched list.
  await page.getByTitle("Edit").first().click();
  const name = page.getByLabel("Name");
  await expect(name).toBeDisabled();
  await page.getByLabel("Description").fill("Monday status report, sharper");
  await page.getByRole("button", { name: "Save skill" }).click();
  await expect(page.getByText("Monday status report, sharper")).toBeVisible();
});

test("skills-settings: disable → amber everywhere/clean-slate banner; delete is two-step", async ({ page }) => {
  await openSkills(page);

  await page.getByLabel("weekly-report enabled").click();
  const status = page.getByRole("status");
  await expect(status).toContainText("weekly-report");
  await expect(status).toContainText("turned off everywhere");
  await expect(status).toContainText("start a new one for a completely clean slate");

  // Two-step delete: arm, confirm, row gone, banner names the skill.
  await page.getByLabel("Delete html-to-markdown").click();
  await expect(page.getByText("html-to-markdown")).toBeVisible(); // armed ≠ deleted
  await page.getByText("Confirm delete").click();
  await expect(page.getByText("html-to-markdown")).toHaveCount(1); // only the banner remains
  await expect(page.getByRole("status")).toContainText("removed");
});
