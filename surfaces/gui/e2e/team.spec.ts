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

test("a rich proposal keeps its actions and composer reachable in a short window", async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 700 });
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose security split");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("itemsreq-card");
  await card.locator("summary").first().click();
  const approve = page.getByTestId("itemsreq-approve");
  await expect(approve).toBeInViewport();
  await expect(page.getByPlaceholder(/Reply to adjust the proposal/)).toBeInViewport();
  const bounds = await card.boundingBox();
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  await approve.click();
  await expect(card).toHaveCount(0);
});

test("the decomposition gate shows items with criteria; approval lands them on the board", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("itemsreq-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Proposed work items — 4");
  await expect(card).toContainText("Acceptance criteria");
  await expect(card.getByText("Verification pass")).toBeVisible();

  // essay-length criteria clamp behind a per-item expander (owner-hit 2026-08-16)
  await expect(card).toContainText("inclusive boundaries verified end to end");
  // the short-criteria items get no toggle
  await expect(page.getByTestId("itemsreq-ac-toggle-1")).toHaveCount(0);

  await page.getByTestId("itemsreq-approve").click();
  await expect(page.getByText(/Items created on the board/)).toBeVisible();
  // Sections start collapsed (a count chip is the maximum signal) — but the lead's
  // one-time board chip now opens the Work pane.
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
  await page.getByTestId("board-chip").click();
  await expect(page.getByTestId("team-view")).toBeVisible();
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

test("a team update renders collapsed; expanded it groups by work item with note previews", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("board wake");
  await page.getByRole("button", { name: "Send" }).click();
  const line = page.getByTestId("team-update");
  await expect(line).toBeVisible();
  await expect(line).toContainText("Team update");
  await expect(line).not.toHaveAttribute("open");
  await line.locator("summary").click();
  await expect(line).toHaveAttribute("open", "");
  await expect(line).toContainText("Fix refund rounding");
  await expect(line).toContainText("Add regression coverage");
  await expect(line).not.toContainText("Sample note");
});

test("declining the split returns feedback to the lead", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("itemsreq-card").waitFor();
  await page.getByRole("button", { name: "Request changes" }).click();
  await page.getByLabel("What should change?").fill("Revise the task split");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText(/reworking the split/)).toBeVisible();
});

test("the staffing gate shows named workers, the chat toggle, and one-line buttons", async ({
  page,
}) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  await expect(card).toContainText("Creating a team of agents — 3 workers");
  // callnames lead the rows; persona + reason follow
  await expect(card).toContainText("nia");
  await expect(card).toContainText("swe-worker");
  await expect(card).toContainText("implementation");
  await expect(card).toContainText("checks");
  // the chat checkbox defaults OFF — the user's call, not the lead's
  await expect(card.getByTestId("teamreq-chat-toggle")).not.toBeChecked();
  // the grant sentence lives in the primary button's title, not in the card's text
  await expect(card).not.toContainText("Approving grants the lead");
  const approve = card.getByTestId("teamreq-approve");
  await expect(approve).toHaveText("Create team");
  await expect(card).toContainText("does not start these tasks");
  await expect(card.getByRole("button", { name: "Request changes" })).toBeVisible();
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
  const chatRow = page.getByTestId("team-chat-action");
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
  await page.getByRole("button", { name: "Request changes" }).click();
  await page.getByLabel("What should change?").fill("Revise the roster");
  await page.getByRole("button", { name: "Send feedback" }).click();
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
  await expect(panel.getByTestId("team-row-nia")).toContainText("nia");
  await expect(panel.getByTestId("team-row-webb")).toContainText("Idle");
  await expect(panel.getByTestId("team-row-checks")).toContainText("Needs attention");

  // Worker inspection keeps the lead alongside; full navigation is explicit.
  await panel.getByTestId("team-row-nia").click();
  await expect(page.getByTestId("team-view")).toBeVisible();
  await expect(page.getByTestId("session-title")).toHaveText("Build the statements page");
  await page.getByRole("button", { name: "Open full session" }).click();
  await expect(page.getByTestId("rail-toggle-progress")).toBeVisible();
  await expect(page.getByTestId("rail-toggle-team")).toHaveCount(0);
  await page.getByRole("button", { name: "Back to lead" }).click();
  await expect(page.getByTestId("session-title")).toHaveText("Build the statements page");
  await expect(page.getByTestId("back-to-lead")).toHaveCount(0);
});

// Token counting (connectors-across-machines §5): the lead's Team panel rolls the tree
// up by model — the lead's live turns plus each worker's persisted totals. Counts only.
test("the Team panel shows tokens by model for the lead and its workers", async ({ page }) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();
  await expect(page.getByTestId("rail-toggle-team")).toBeVisible({ timeout: 12_000 });
  await page.getByTestId("rail-toggle-team").click();

  const usage = page.getByTestId("team-usage");
  await expect(usage).toBeVisible();
  // nia's persisted 24k plus the lead's own turns so far (the fixture's 10k per turn).
  await expect(usage).toContainText("Tokens");
  await expect(usage).toContainText("claude-opus-4-8");
  await expect(usage).not.toContainText("$");
});

// Worker-connector-grants spec §2, §6: workers start with no connectors; the card offers
// each worker's default set — the lead's suggestion arrives TICKED with its reason, the
// rest unticked — and everything else connected on the machine behind "＋ Add another
// connector". Approvals follow the lead: the card states the lead's CURRENT mode, live from the composer,
// and has no control of its own. The human's connector decisions ride the approval.
test("the staffing card carries connector checkboxes and states that approvals follow the lead", async ({
  page,
}) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  // the lead suggested github for nia → ticked, with the lead's reason beside it
  const nia = card.getByTestId("teamreq-connector-0-github");
  await expect(nia).toBeChecked();
  await expect(card.getByTestId("teamreq-row-0")).toContainText("pushes the branch and opens the PR");
  // no suggestion for checks → its default set is offered unticked, with no reason
  await expect(card.getByTestId("teamreq-connector-2-github")).not.toBeChecked();
  // webb declares nothing: no default group, but the machine's other connectors can be added
  await expect(card.getByTestId("teamreq-row-1")).not.toContainText("none available");
  await expect(card.getByTestId("teamreq-beyond-1")).toHaveCount(0);
  await card.getByTestId("teamreq-add-1").click();
  await card.getByTestId("teamreq-connector-1-linear").check();
  await expect(card.getByTestId("teamreq-beyond-1")).toContainText("Beyond this worker's usual set");
  await expect(card.getByTestId("teamreq-connector-1-linear")).toBeChecked();
  await expect(card.getByTestId("proposal-connector-summary")).toContainText("GitHub");
  // the approvals line reads the composer's mode…
  await expect(card.getByTestId("teamreq-approvals")).toContainText("Approvals follow the lead's mode: Ask for approval");
  // …and follows it live when the mode changes in the composer
  await page.getByRole("button", { name: "Mode" }).click();
  await page.getByTestId("mode-menu").getByText("Bypass approvals").click();
  await expect(card.getByTestId("teamreq-approvals")).toContainText("Approvals follow the lead's mode: Bypass approvals");
  // approve as ticked — the final ticks ride the response, beyond-default ones included
  await page.getByTestId("teamreq-approve").click();
  await expect(
    page.getByText("Team created — nia (github), webb (linear), checks (no connectors)"),
  ).toBeVisible();
});

// Worker models: each worker's model is picked on the card. A worker whose persona
// recommends nothing runnable arrives on the lead's model with a warning glyph; picking
// another model clears it, and the final pick per worker rides the approval.
test("the staffing card picks each worker's model and warns on a fallback", async ({ page }) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  await expect(card.getByTestId("teamreq-model-0")).toHaveValue("anthropic:claude-opus-4-8");
  await expect(card.getByTestId("teamreq-model-warn-0")).toHaveCount(0);
  const warn = card.getByTestId("teamreq-model-warn-2");
  await expect(warn).toBeVisible();
  await expect(warn).toHaveAttribute("title", /recommended models can run on this machine/);
  // the picker sits on line one without making the row wrap
  const who = card.getByTestId("teamreq-row-2").locator(".teamreq-who");
  const code = await who.locator("code").boundingBox();
  const pick = await card.getByTestId("teamreq-model-2").boundingBox();
  expect(Math.abs((code!.y + code!.height / 2) - (pick!.y + pick!.height / 2))).toBeLessThan(6);
  await card.getByTestId("teamreq-model-2").selectOption("anthropic:claude-haiku-4-8");
  await expect(card.getByTestId("teamreq-model-warn-2")).toHaveCount(0);
  // a pick outside the worker's recommended models is noted, quietly
  await card.getByTestId("teamreq-model-1").selectOption("anthropic:claude-opus-4-8");
  await expect(card.getByTestId("teamreq-model-note-1")).toHaveText(
    "Not one of this worker's recommended models.",
  );
  await page.getByTestId("teamreq-approve").click();
  await expect(
    page.getByText(
      "Models — nia: anthropic:claude-opus-4-8, webb: anthropic:claude-opus-4-8, checks: anthropic:claude-haiku-4-8.",
    ),
  ).toBeVisible();
});

test("a lead may ask for a worker's connector later; the human grants or declines", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("grant nia github");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("connreq-card");
  await expect(card).toContainText("give nia access to GitHub");
  await expect(card).toContainText("push the branch for #1");
  await page.getByTestId("connreq-grant").click();
  await expect(page.getByText("Granted — nia can push the branch now.")).toBeVisible();
  await expect(page.getByTestId("connreq-card")).toHaveCount(0);
});

test("a coworker may ask for a service to be connected; not now is a plain outcome", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("connect linear");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("connreq-card");
  await expect(card).toContainText("would like Linear connected");
  await expect(card.getByTestId("connreq-open")).toBeVisible();
  await page.getByTestId("connreq-decline").click();
  await expect(page.getByText("route that step through myself")).toBeVisible();
});
