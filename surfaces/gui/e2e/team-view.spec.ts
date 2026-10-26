import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`right panel opens team tabs and workers beside the lead (${theme})`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem("coworker:rail-hidden:v1", "0");
      localStorage.setItem("openwork-theme", value);
    }, theme);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/?scenario=team-view-5#/s/scn-team-view-5");
    const toggle = page.getByTestId("rail-toggle-team");
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(page.getByTestId("team-chat-off")).toHaveCount(0);
    await page.getByTestId("team-chat-action").click();
    await expect(page.getByTestId("team-chat-off")).toBeVisible();
    await page.getByRole("button", { name: "Dismiss", exact: true }).click();
    await page.getByTestId("rail-open-team-view").click();
    const pane = page.getByTestId("team-view");
    await expect(pane.getByRole("tab")).toHaveCount(3);
    await pane.getByRole("button", { name: "Close team view" }).click();
    await page.screenshot({ path: `test-results/team-rail-${theme}.png` });
    await page.getByTestId("team-row-sam").click();
    await expect(pane.getByRole("heading", { name: "sam", exact: true })).toBeVisible();
    await expect(page).toHaveURL(/#\/s\/scn-team-view-5$/);
    await expect(page.getByTestId("session-title")).toHaveText("Refund rounding · Acme");
    await expect.poll(() => page.locator(".sidebar").evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `test-results/team-rail-worker-${theme}.png` });
    await pane.getByRole("button", { name: "Open full session" }).click();
    await expect(page).toHaveURL(/#\/s\/scn-team-view-5-sam$/);
    await page.getByRole("button", { name: "Back to lead" }).click();
    await expect(page).toHaveURL(/#\/s\/scn-team-view-5$/);
  });
}

test("a directly opened worker retains a route back to its lead after reload", async ({ page }) => {
  await page.goto("/?scenario=team-view-5#/s/scn-team-view-5-sam");
  await expect(page.getByTestId("session-title")).toHaveText("sam");
  await expect(page.getByRole("button", { name: "Back to lead" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Back to lead" })).toBeVisible();
  await page.getByRole("button", { name: "Back to lead" }).click();
  await expect(page.getByTestId("session-title")).toHaveText("Refund rounding · Acme");
  await expect(page).toHaveURL(/#\/s\/scn-team-view-5$/);
  await expect(page.getByTestId("back-to-lead")).toHaveCount(0);
});

test("team icon, task chip, worker message and all three tabs work together", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?scenario=team-view-5#/s/scn-team-view-5");
  await expect(page.getByTestId("team-created")).toBeVisible();
  await expect(page.getByTestId("team-update")).toHaveCount(1);
  await expect(page.getByTestId("team-update")).toContainText(
    "Team updates (2)",
  );
  await page
    .getByRole("button", { name: "Team quick look", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Team quick look" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open team view" }).click();
  const pane = page.getByTestId("team-view");
  await expect(pane).toBeVisible();
  await expect(pane.getByText("1 task is waiting on you.")).toBeVisible();
  await page.screenshot({ path: "test-results/team-view-work.png" });
  await pane.getByTestId("team-task-1").click();
  await expect(pane.getByTestId("board-detail")).toBeVisible();
  await expect(pane.getByTestId("team-worker-transcript")).toHaveCount(0);
  await expect(pane.locator(".composer")).toHaveCount(0);
  await expect(
    pane.getByText(/The regression covers partial refunds/),
  ).toHaveCount(0);
  await page.screenshot({ path: "test-results/team-view-task.png" });
  await pane.getByTestId("board-open-worker").click();
  await expect(
    pane.getByRole("heading", { name: "sam", exact: true }),
  ).toBeVisible();
  await expect(pane.getByTestId("board-detail")).toHaveCount(0);
  await expect(
    pane.getByText(
      "Check the invoice refund behavior against the approved criteria.",
    ),
  ).toHaveCount(0);
  await expect(pane.locator(".composer")).toBeVisible();
  await expect(pane.getByTestId("mode-follows-lead")).toBeVisible();
  await expect(
    pane.getByText(/The regression covers partial refunds/),
  ).toBeVisible();
  const workerInput = pane.getByRole("textbox", { name: "Message sam…" });
  await workerInput.fill("Check keyboard navigation too.");
  await pane.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByTestId("scenario-sent-toggle").click();
  await expect(page.getByTestId("scenario-sent")).toContainText(
    "/ws/session/scn-team-view-5-sam",
  );
  await expect(page.getByTestId("scenario-sent")).toContainText(
    "Check keyboard navigation too.",
  );
  await page.getByTestId("scenario-sent-toggle").click();
  await page.screenshot({ path: "test-results/team-view-worker.png" });
  await pane.getByRole("button", { name: "← Team" }).click();
  await pane.getByRole("tab", { name: "Workers" }).click();
  await expect(pane.getByTestId("team-worker-sam")).toBeVisible();
  await pane.getByTestId("team-worker-sam").click();
  await expect(
    pane.getByRole("heading", { name: "sam", exact: true }),
  ).toBeVisible();
  await expect(pane.getByTestId("board-detail")).toHaveCount(0);
  await pane.getByText("Assigned tasks (1)", { exact: true }).click();
  await pane
    .getByRole("button", { name: "Fix refund rounding", exact: true })
    .click();
  await expect(pane.getByTestId("board-detail")).toBeVisible();
  await expect(pane.getByTestId("team-worker-transcript")).toHaveCount(0);
  await pane.getByRole("button", { name: "← Team" }).click();
  await pane.getByRole("tab", { name: "Stats" }).click();
  await pane.getByRole("button", { name: "Show tokens over time" }).click();
  await expect(pane.getByRole("img")).toBeVisible();
  await page.screenshot({ path: "test-results/team-view-stats.png" });
  await pane.getByRole("button", { name: "Close team view" }).click();
  await expect(pane).toHaveCount(0);
  await page
    .locator('.bubble-assistant [data-testid="task-chip-3"]')
    .first()
    .click();
  await expect(
    page
      .getByTestId("team-view")
      .getByRole("heading", { name: "Backfill affected invoices" }),
  ).toBeVisible();
});

test("large team groups workers and shows top five token consumers", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?scenario=team-view-100#/s/scn-team-view-100");
  await page
    .getByRole("button", { name: "Team quick look", exact: true })
    .click();
  const quick = page.getByRole("region", { name: "Team quick look" });
  await expect(quick.getByTestId(/team-task-/)).toHaveCount(1);
  await expect(quick.getByText("swe-worker × 40")).toBeVisible();
  await page.screenshot({ path: "test-results/team-view-large-quick.png" });
  await quick.getByRole("button", { name: "Open team view" }).click();
  const pane = page.getByTestId("team-view");
  await pane.getByRole("tab", { name: "Workers" }).click();
  await expect(pane.getByTestId(/^team-worker-/).filter({ hasNot: page.locator("textarea") })).toHaveCount(8);
  await expect(pane.getByRole("searchbox")).toBeVisible();
  await expect(pane.getByTestId("team-worker-sam")).toBeVisible();
  await pane.getByRole("tab", { name: "Stats" }).click();
  await expect(pane.getByText("Top five of 100 workers")).toBeVisible();
  await pane.getByRole("button", { name: "By role" }).click();
  await expect(pane.getByText("Top five of 100 workers")).toHaveCount(0);
  await page.screenshot({ path: "test-results/team-view-large-stats.png" });
});

test("quick look is keyboard controlled and does not move the composer", async ({
  page,
}) => {
  await page.goto("/?scenario=team-view-5#/s/scn-team-view-5");
  const icon = page.getByRole("button", {
    name: "Team quick look",
    exact: true,
  });
  const composer = page.getByPlaceholder(/Ask the coworker/);
  const before = await composer.boundingBox();
  const iconBox = await icon.boundingBox();
  const textInset = await composer.evaluate(el => parseFloat(getComputedStyle(el).paddingLeft));
  expect(Math.abs(iconBox!.x - before!.x - textInset)).toBeLessThanOrEqual(1);
  await icon.hover();
  expect(await composer.boundingBox()).toEqual(before);
  await icon.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("region", { name: "Team quick look" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("region", { name: "Team quick look" }),
  ).toHaveCount(0);
  await expect(icon).toBeFocused();
});
