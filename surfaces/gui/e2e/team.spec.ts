// Agent teams (OPE-97): the staffing gate + the drawer's Team panel (seventeenth
// pass). The fake lead proposes a roster on "staff the team" and suspends; approval
// "pre-spawns" workers (the fixture mirrors create_team by adding worker sessions),
// which surface in the right drawer's Team section — the sidebar keeps ONE entry
// per team (the lead), with no expansion.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function proposeTeam(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("staff the team");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("teamreq-card")).toBeVisible();
}

test("the decomposition gate shows items with criteria; approval lands them on the board", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("itemsreq-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Proposed work items — 4");
  await expect(card).toContainText("Done when:");
  // 3 visible + expander with the true remainder
  await expect(card.getByText("Verification pass")).toHaveCount(0);
  await card.getByRole("button", { name: /1 more item/ }).click();
  await expect(card.getByText("Verification pass")).toBeVisible();

  // essay-length criteria clamp behind a per-item expander (owner-hit 2026-08-16)
  const acToggle = page.getByTestId("itemsreq-ac-toggle-0");
  await expect(acToggle).toHaveText("Show full criteria");
  await acToggle.click();
  await expect(acToggle).toHaveText("Show less");
  // the short-criteria items get no toggle
  await expect(page.getByTestId("itemsreq-ac-toggle-1")).toHaveCount(0);

  await page.getByTestId("itemsreq-approve").click();
  await expect(page.getByText(/Items created on the board/)).toBeVisible();
  // Sections start collapsed (a count chip is the maximum signal) — but the lead's
  // one-time [Board · N items](board:) chip expands the drawer's Board section.
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
  await page.getByTestId("board-chip").click();
  await expect(page.getByTestId("board-rail")).toBeVisible();
});

test("typing while a gate is pending sends the reply as feedback to the lead", async ({
  page,
}) => {
  await proposeTeam(page);
  // the composer re-opens for a typed answer instead of hard-blocking on "running"
  const box = page.getByPlaceholder(/Reply to adjust the proposal/);
  await box.fill("use openai:gpt-5.6-sol for all the workers");
  await page.getByRole("button", { name: "Send" }).click();
  // the reply lands as a user message AND resolves the gate as decline-with-feedback
  await expect(
    page.getByText("use openai:gpt-5.6-sol for all the workers"),
  ).toBeVisible();
  await expect(page.getByText(/tell me how to change the roster/)).toBeVisible();
  await expect(page.getByTestId("teamreq-card")).toHaveCount(0);
});

test("a board wake renders collapsed; expanding reveals rows, hand-offs stay one more click away", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("board wake");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("boardwake-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Board wake");
  await expect(card).toContainText("1 review, 1 filing");
  // collapsed by default: ambient awareness, not reading assignment
  await expect(page.getByTestId("boardwake-body")).toHaveCount(0);
  await expect(card).not.toContainText("029f9f7");
  await page.getByTestId("boardwake-toggle").click();
  const body = page.getByTestId("boardwake-body");
  await expect(body).toBeVisible();
  await expect(body).toContainText("#2 Statements page → review by webb");
  await expect(body).toContainText("nia filed #5 Follow-up: rate limit");
  // the hand-off comment sits behind its own per-row toggle
  await expect(body).not.toContainText("029f9f7");
  await body.getByRole("button", { name: "show hand-off" }).click();
  await expect(body).toContainText("029f9f7");
});

test("declining the split returns feedback to the lead", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("itemsreq-card").waitFor();
  await page.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByText(/reworking the split/)).toBeVisible();
});

test("the staffing gate shows named workers, the chat toggle, and the grant sentence", async ({
  page,
}) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  await expect(card).toContainText("Proposed team — 3 workers");
  // callnames lead the rows; persona + reason follow
  await expect(card).toContainText("nia");
  await expect(card).toContainText("swe-worker");
  await expect(card).toContainText("implementation");
  await expect(card).toContainText("checks");
  // the chat checkbox defaults OFF — the user's call, not the lead's
  await expect(card.getByTestId("teamreq-chat-toggle")).not.toBeChecked();
  await expect(card).toContainText(
    "Approving grants the lead create, assign & steer — this team only, revocable.",
  );
});

test("enabling chat at the gate adds the # team chat row; posting works with mentions", async ({
  page,
}) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-chat-toggle").check();
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();

  // The chat row lives in the drawer's Team panel now (sessions poll: allow a cycle).
  await expect(page.getByTestId("rail-toggle-team")).toBeVisible({ timeout: 12_000 });
  await page.getByTestId("rail-toggle-team").click();
  const chatRow = page.getByTestId("team-chat-row");
  await expect(chatRow).toBeVisible();
  await expect(chatRow).toContainText("1"); // unread badge

  await chatRow.click();
  const view = page.getByTestId("teamchat-view");
  await expect(view).toBeVisible();
  await expect(view).toContainText("assets bucket is public");
  await expect(view.locator(".chat-mention").first()).toHaveText("@nia");

  await page.getByTestId("chat-input").fill("ship it current-month only @lead");
  await page.getByTestId("chat-send").click();
  await expect(view).toContainText("ship it current-month only");

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("teamchat-view")).toHaveCount(0);
});

test("a sleeping lead shows the strip; Ask for a status wakes it", async ({ page }) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();
  // open the lead's session — it set a check-in timer, so it's sleeping
  await page.locator(".sidebar").getByText("Build the statements page").click();
  const strip = page.getByTestId("sleep-strip");
  await expect(strip).toBeVisible({ timeout: 12_000 });
  await expect(strip).toContainText("Sleeping until");
  await expect(strip).toContainText("while the team works");
  await page.getByTestId("sleep-status-btn").click();
  await expect(page.getByText(/Echo: Quick status check/)).toBeVisible();
});

test("with chat declined at the gate, no chat row renders", async ({ page }) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();
  await expect(page.getByTestId("rail-toggle-team")).toBeVisible({ timeout: 12_000 });
  await page.getByTestId("rail-toggle-team").click();
  await expect(page.getByTestId("team-panel")).toBeVisible();
  await expect(page.getByTestId("team-chat-row")).toHaveCount(0);
});

test("declining the roster returns the turn to the lead", async ({ page }) => {
  await proposeTeam(page);
  await page.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByText(/tell me how to change the roster/)).toBeVisible();
  await expect(page.getByTestId("teamreq-card")).toHaveCount(0);
});

test("approval creates the team; members live in the drawer, RECENT keeps one entry", async ({
  page,
}) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();

  // The drawer grows a collapsed Team section with a member-count chip.
  // (Sessions poll every 5s, so allow one full cycle.)
  const teamToggle = page.getByTestId("rail-toggle-team");
  await expect(teamToggle).toBeVisible({ timeout: 12_000 });
  await expect(teamToggle).toContainText("3");
  await expect(page.getByTestId("team-panel")).toHaveCount(0); // collapsed by default

  // The lead is the SESSION — Progress yields its slot (the board is the lead's
  // progress surface).
  await expect(page.getByTestId("rail-toggle-progress")).toHaveCount(0);

  // Workers never appear as top-level RECENT rows — one entry per team, no expansion.
  const sidebar = page.locator(".sidebar");
  await expect(sidebar.getByText("Build the statements page")).toBeVisible();
  await expect(sidebar.getByText("nia", { exact: true })).toHaveCount(0);
  await expect(sidebar.locator("[data-testid^=team-toggle-]")).toHaveCount(0);

  // Expanding the Team panel shows member rows: dot + callname + current item.
  await teamToggle.click();
  const panel = page.getByTestId("team-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("team-row-nia")).toContainText("#1 in progress");
  await expect(panel.getByTestId("team-row-webb")).toContainText("idle");
  await expect(panel.getByTestId("team-row-checks")).toContainText("#4 blocked");

  // A member row is the escape hatch — clicking opens that worker's session, where
  // the drawer is a plain worker drawer again (Progress back, no Team panel).
  await panel.getByTestId("team-row-nia").click();
  await expect(page.getByTestId("rail-toggle-progress")).toBeVisible();
  await expect(page.getByTestId("rail-toggle-team")).toHaveCount(0);
});
