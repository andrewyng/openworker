import { test, expect } from "./fixtures";

// Which coworker am I talking to? Before this, nothing on the session screen answered that: the
// composer said "Ask the coworker…" whichever persona was active, every session was the same
// cobalt, and the start screen offered the DEFAULT persona's three tasks (folder / HubSpot /
// GitHub→Slack) even to a code persona that can't do any of them.
//
// A persona's identity now shows in four places at once, all from the one persona record: the
// start screen (its greeting + its own tasks), the composer placeholder, the chip on the control
// row, and the accent the session is tinted with.

async function startAs(page: import("@playwright/test").Page, persona: RegExp) {
  await page.getByLabel("Choose a persona").click();
  await page.locator(".newsplit-menu").getByRole("button", { name: persona }).click();
  // A persona that belongs to a project opens the folder gate first, and its overlay swallows
  // clicks on the session beneath. Choose a folder so the session is actually reachable.
  const gate = page.locator(".gate-overlay");
  if (await gate.isVisible().catch(() => false)) {
    await gate.getByPlaceholder("/path/to/your/project").fill("/tmp/e2e-persona-project");
    await gate.getByRole("button", { name: "Open", exact: true }).click();
    await expect(gate).toHaveCount(0);
  }
}

test("the session states which persona is answering, and switching changes all of it", async ({
  page,
}) => {
  await page.goto("/");

  // Default coworker: its own greeting + tasks, its own placeholder, cobalt.
  await expect(page.getByText("What should we produce?")).toBeVisible();
  await expect(page.getByTestId("composer-persona")).toHaveText("Coworker");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();
  await expect(page.locator(".main")).toHaveAttribute("data-accent", "cobalt");

  await startAs(page, /Ops/);

  // Everything follows the persona — including the tasks, which are Ops's now, not Coworker's.
  await expect(page.getByText("What's going on?")).toBeVisible();
  await expect(page.getByTestId("intro-task-health")).toBeVisible();
  await expect(page.getByTestId("intro-task-hubspot")).toHaveCount(0);
  await expect(page.getByTestId("composer-persona")).toHaveText("Ops");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();
  await expect(page.locator(".main")).toHaveAttribute("data-accent", "teal");
});

test("a persona's own task prefills the composer; a gated one offers setup instead", async ({
  page,
}) => {
  await page.goto("/");
  await startAs(page, /Ops/);

  // Datadog is not connected in the fixture → that row is gated and prefills nothing.
  const gated = page.getByTestId("intro-task-alerts");
  await expect(gated).toContainText("Configure ›");
  await gated.click();
  await expect(page.getByRole("region", { name: "Session access" })).toBeVisible();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toHaveValue("");

  // The row that needs nothing starts immediately.
  await page.getByTestId("intro-task-health").click();
  await expect(box).toHaveValue(/Check the health of the service/);
});

test("the persona chip opens that persona's page", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("composer-persona").click();
  await expect(page.getByRole("button", { name: "Back", exact: true })).toBeVisible();
});
