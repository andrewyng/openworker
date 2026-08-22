// The Automations page is a grid plus a marketplace: tiles grouped by cadence, and above them
// suggestions the SERVER derived from this machine's own activity. A suggestion leads with its
// evidence ("19 commits to acme, nothing watches it") — that line is what separates it from a
// template, which is the same card for everybody and lives behind a disclosure.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openAutomations(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByTestId("nav-automations").click();
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
}

test("suggestions lead with their evidence and sit above the schedule", async ({ page }) => {
  await openAutomations(page);
  const shelf = page.getByTestId("suggestion-shelf");
  await expect(shelf).toBeVisible();

  const repo = page.getByTestId("suggestion-repo-health-acme");
  await expect(repo).toContainText("Repo health — acme");
  await expect(repo).toContainText("19 of your commits to acme");
  await expect(repo).toContainText("weekly");

  // A suggestion that needs a connector says so rather than failing later.
  await expect(page.getByTestId("suggestion-connector-slack")).toContainText("needs slack");
});

test("accepting a suggestion schedules it and stops suggesting it", async ({ page }) => {
  await openAutomations(page);
  await page.getByTestId("add-repo-health-acme").click();

  // Creating opens the new automation's detail…
  await expect(page.getByRole("heading", { name: "Repo health — acme" })).toBeVisible();

  // …and back on the list it is scheduled, not suggested. Re-suggesting something the user
  // already acted on reads as the machine not paying attention.
  await page.getByRole("button", { name: "← Automations" }).click();
  await expect(page.locator(".sched-tile", { hasText: "Repo health — acme" })).toHaveCount(1);
  await expect(page.getByTestId("suggestion-repo-health-acme")).toHaveCount(0);
  await expect(page.getByTestId("suggestion-connector-slack")).toBeVisible();
});

test("'not now' dismisses one suggestion without touching the others", async ({ page }) => {
  await openAutomations(page);
  await page
    .getByTestId("suggestion-connector-slack")
    .getByRole("button", { name: "not now" })
    .click();
  await expect(page.getByTestId("suggestion-connector-slack")).toHaveCount(0);
  await expect(page.getByTestId("suggestion-repo-health-acme")).toBeVisible();
});

test("generic templates stay behind a disclosure", async ({ page }) => {
  await openAutomations(page);
  // Not shown by default: a template grid under a full schedule is noise.
  await expect(page.getByText("Start from a template")).toHaveCount(0);
  await page.getByTestId("browse-templates").click();
  await expect(page.getByText("Start from a template")).toBeVisible();
});
