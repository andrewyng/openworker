// Standalone boards retain their entry point; detail and verdicts now use the shared pane.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
async function planTheWork(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/filed 5 work items/)).toBeVisible();
}
async function openTask(page: import("@playwright/test").Page, id: number) {
  await page.getByTestId("board-expand").click();
  await page
    .getByTestId("team-view")
    .getByTestId("team-task-" + id)
    .click();
  await expect(page.getByTestId("board-detail")).toBeVisible();
}
test("plain sessions carry zero board chrome", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello")).toBeVisible();
  await expect(page.getByTestId("rail-toggle-board")).toHaveCount(0);
});

test("published reports are downloadable evidence alongside screenshots", async ({ page }) => {
  const stored = "a".repeat(64) + ".md";
  await page.route("**/board/item?*", async route => {
    await route.fulfill({ json: {
      id: 5, title: "Verify billing", description: "", criteria: "Independent evidence", state: "review",
      assignee: "maya", creator: "lead", refs: [], links: [],
      timeline: [{ seq: 100, ts: new Date().toISOString(), actor: "maya", kind: "comment",
        body: "Acceptance report", refs: [`attachment://${stored}#findings.md`] }],
    } });
  });
  await page.route("**/board/attachment?*", route => route.fulfill({
    contentType: "text/plain", body: "# Acceptance\nAll criteria passed on the submitted revision.\n",
  }));
  await planTheWork(page);
  await openTask(page, 5);
  const link = page.getByTestId("board-file-attachment");
  await expect(link).toHaveText("findings.md");
  await expect(page.getByTestId("board-detail").getByRole("img")).toHaveCount(0);
  const downloaded = page.waitForEvent("download");
  await link.click();
  expect((await downloaded).suggestedFilename()).toBe("findings.md");
  await page.screenshot({ path: "test-results/board-file-artifact.png" });
});
test("standalone boards remain accessible while the full-window overlay is retired", async ({
  page,
}) => {
  await planTheWork(page);
  await page.getByTestId("rail-toggle-board").click();
  await expect(page.getByTestId("board-rail")).toContainText("Queued");
  await page.getByTestId("board-expand").click();
  await expect(page.getByTestId("team-view")).toBeVisible();
  await expect(page.getByTestId("board-overlay")).toHaveCount(0);
  await expect(page.getByTestId("team-task-4")).toContainText("need tfvars");
  await expect(
    page.getByTestId("team-view").getByRole("button", { name: "Mark done" }),
  ).toHaveCount(0);
});
test("review verdict and remove still round-trip from task detail", async ({
  page,
}) => {
  await planTheWork(page);
  await openTask(page, 5);
  await page
    .getByTestId("board-detail")
    .getByRole("button", { name: "Mark done" })
    .click();
  await expect(page.getByTestId("board-detail")).toContainText("Done");
  await page
    .getByTestId("team-view")
    .getByRole("button", { name: "← Team" })
    .click();
  await page.getByTestId("team-task-1").click();
  await page
    .getByTestId("board-detail")
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(page.getByTestId("board-detail")).toContainText("Canceled");
  await page
    .getByTestId("team-view")
    .getByRole("button", { name: "Close team view" })
    .click();
  await expect(page.getByTestId("team-view")).toHaveCount(0);
});
test("timeline attachments and request changes survive the new pane", async ({
  page,
}) => {
  await planTheWork(page);
  await openTask(page, 5);
  const detail = page.getByTestId("board-detail");
  await expect(detail).toContainText(
    "balances reconcile against the seeded rows",
  );
  await expect(detail.getByTestId("board-attachment")).toBeVisible();
  await detail.getByRole("button", { name: "Request changes…" }).click();
  await detail
    .getByPlaceholder("What needs to change?")
    .fill("Check the totals for Sam");
  await detail
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await expect(detail).toContainText("In progress");
});
test("notes append without changing state, and failed saves preserve the draft", async ({
  page,
}) => {
  await planTheWork(page);
  await openTask(page, 5);
  const detail = page.getByTestId("board-detail");
  await detail.getByTestId("board-note-input").fill("Check the v2 totals");
  await detail.getByTestId("board-note-input").press("Enter");
  await expect(detail).toContainText("Check the v2 totals");
  await expect(detail).toContainText("In review");
  await page.route("**/board/comment", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: '{"error":"unavailable"}',
    }),
  );
  await detail.getByTestId("board-note-input").fill("Keep this draft");
  await detail.getByTestId("board-note-input").press("Enter");
  await expect(page.getByRole("alert")).toContainText("could not be completed");
  await expect(detail.getByTestId("board-note-input")).toHaveValue(
    "Keep this draft",
  );
});
test("journal section remains available for a standalone board", async ({
  page,
}) => {
  await planTheWork(page);
  await page.getByTestId("rail-toggle-journal").click();
  await expect(page.getByTestId("journal-list")).toContainText("findings");
  await expect(page.getByTestId("journal-list")).toContainText("12 entries");
});
