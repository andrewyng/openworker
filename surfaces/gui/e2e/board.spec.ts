// Agent teams: the board in the session UI — the rail section (grouped by state,
// blocked on top, active work only) and the expanded overlay: a quiet list over
// the store's RAW states (In progress / Awaiting review / Queued — no computed
// interpretation layer, no row buttons) plus a detail pane with the item's merged
// event timeline. Verdicts flow through the pane: Mark done / Request changes….
// The fake agent files items on "plan the work"; transitions round-trip through
// the mocked /board endpoints as the user.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function planTheWork(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/filed 5 work items/)).toBeVisible();
}

// Seventeenth pass: every drawer section starts collapsed — expanding the Board
// section is now an explicit step wherever a test reads the rail's rows.
async function openBoardSection(page: import("@playwright/test").Page) {
  await page.getByTestId("rail-toggle-board").click();
  await expect(page.getByTestId("board-rail")).toBeVisible();
}

test("plain sessions carry zero board chrome", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello")).toBeVisible();
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
  await expect(page.getByTestId("rail-toggle-board")).toHaveCount(0);
});

test("filed items appear grouped in the rail, blocked on top, queued items listed", async ({
  page,
}) => {
  await planTheWork(page);
  // collapsed by default: the header chip is the maximum signal
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
  await expect(page.getByTestId("rail-toggle-board")).toContainText("1 blocked · 1 review");
  await openBoardSection(page);
  const rail = page.getByTestId("board-rail");
  await expect(rail).toBeVisible();
  const groups = rail.locator(".board-group");
  await expect(groups.first()).toHaveText("Blocked");
  await expect(rail).toContainText("Queued");
  await expect(rail.getByText("Secrets — git history, both repos")).toBeVisible();
});

test("the overlay lists raw-state sections; verdicts flow through the detail pane", async ({
  page,
}) => {
  await planTheWork(page);
  await page.getByTestId("board-expand").click();
  const overlay = page.getByTestId("board-overlay");
  await expect(overlay).toBeVisible();
  // the owner's sections, nothing computed — and no buttons in the rows
  await expect(overlay).toContainText("In progress");
  await expect(overlay).toContainText("Awaiting review");
  await expect(overlay).toContainText("Queued");
  await expect(overlay.getByRole("button", { name: "Mark done" })).toHaveCount(0);
  // a blocked row carries the blocker as a plain fact under In progress
  await expect(page.getByTestId("board-item-4")).toContainText(
    "cloud-posture · blocked: need tfvars for staging",
  );

  // review verdict from the pane
  await page.getByTestId("board-item-5").click();
  const detail = page.getByTestId("board-detail");
  await detail.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByTestId("overlay-finished-toggle")).toHaveText("1 finished · show");

  // queued items are removed from their pane (maps to canceled underneath)
  await page.getByTestId("board-item-1").click();
  await detail.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByTestId("overlay-finished-toggle")).toHaveText("2 finished · show");

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("board-overlay")).toHaveCount(0);
});

test("finished items leave the rail; a quiet toggle reveals them", async ({ page }) => {
  await planTheWork(page);
  await openBoardSection(page);
  const rail = page.getByTestId("board-rail");
  await expect(rail.getByText("Report rollup")).toBeVisible(); // review = active
  await page.getByTestId("board-expand").click();
  await page.getByTestId("board-item-5").click();
  await page.getByTestId("board-detail").getByRole("button", { name: "Mark done" }).click();
  await page.keyboard.press("Escape");
  // done vanishes from the rail — a fresh session on an old board starts calm
  await expect(rail.getByText("Report rollup")).toHaveCount(0);
  const toggle = page.getByTestId("board-finished-toggle");
  await expect(toggle).toHaveText("1 finished · show");
  await toggle.click();
  await expect(rail.getByText("Report rollup")).toBeVisible();
  await toggle.click();
  await expect(rail.getByText("Report rollup")).toHaveCount(0);
});

test("item detail: timeline with attachment, worker link, request changes", async ({
  page,
}) => {
  await planTheWork(page);
  await openBoardSection(page);
  // a rail row deep-opens the overlay on that item's detail
  await page.getByTestId("board-rail").getByText("Report rollup").click();
  const detail = page.getByTestId("board-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("#5");
  await expect(detail).toContainText("Report rollup");
  await expect(detail).toContainText("In review");
  await expect(detail).toContainText("Done when");
  // the merged timeline tells the item's whole story
  await expect(detail).toContainText("security started");
  await expect(detail).toContainText("balances reconcile against the seeded rows");
  await expect(detail).toContainText("moved to in review");
  // the attachment image actually loads (real bytes from the fixture)
  await expect(detail.getByTestId("board-attachment")).toBeVisible();
  // the assignee links to that coworker's session
  await expect(detail.getByTestId("board-open-worker")).toHaveText("security ↗");
  // Request changes… discloses a comment box; sending returns the item to work
  await detail.getByRole("button", { name: "Request changes…" }).click();
  await detail.getByPlaceholder("What needs to change?").fill("totals drift on Tom");
  await detail.getByRole("button", { name: "Request changes", exact: true }).click();
  await expect(detail).toContainText("In progress");
  // switching rows switches the pane
  await page.getByTestId("board-item-3").click();
  await expect(detail).toContainText("Dependency audit — lockfiles");
});

test("Add a note is a pure append — it lands in the timeline, state untouched", async ({
  page,
}) => {
  await planTheWork(page);
  await openBoardSection(page);
  await page.getByTestId("board-rail").getByText("Report rollup").click();
  const detail = page.getByTestId("board-detail");
  await expect(detail).toContainText("In review");
  await detail.getByTestId("board-note-input").fill("prefer the v2 endpoint for totals");
  await detail.getByTestId("board-note-input").press("Enter");
  // the note appears as a timeline event…
  await expect(detail).toContainText("user commented");
  await expect(detail).toContainText("prefer the v2 endpoint for totals");
  // …and the state did NOT change (notes never transition)
  await expect(detail).toContainText("In review");
  await expect(detail.getByRole("button", { name: "Mark done" })).toBeVisible();
});

test("journal section lists cases once a board exists", async ({ page }) => {
  await planTheWork(page);
  // Journal is not a primary section — it sits behind the quiet More row.
  await expect(page.getByTestId("rail-toggle-journal")).toHaveCount(0);
  await page.getByTestId("rail-toggle-journal").click();
  const journal = page.getByTestId("journal-list");
  await expect(journal).toBeVisible();
  await expect(journal).toContainText("findings");
  await expect(journal).toContainText("12 entries");
  // Access folds with it — the drawer keeps three primary sections.
  await expect(page.getByTestId("access-section")).toBeVisible();
});
