// The dev-only card gallery (/#/gallery): real cards rendered from the payload files
// under src/gallery/states, with no server at all — so these tests mock nothing.
//
// Element screenshots for review come from here too, in place of throwaway specs:
//   GALLERY_SHOT="team-request/model-fallback,approval/web-fetch?place=inbox" \
//   GALLERY_SHOT_DIR=/tmp/shots GALLERY_THEME=dark npx playwright test card-gallery
// GALLERY_CLICK="testid,testid" clicks those controls on the card before the shot.
import { expect, test } from "@playwright/test";
import path from "node:path";

test("a state link opens that card and state, and the card's answer is printed, not sent", async ({ page }) => {
  const calls: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/v1/") || r.url().includes(":8765")) calls.push(r.url());
  });
  await page.goto("/#/gallery/team-request/lead-suggested-github");
  await expect(page.getByTestId("gallery-state-lead-suggested-github")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("teamreq-connector-0-github")).toBeChecked();

  await page.getByTestId("teamreq-approve").click();
  const sent = page.getByTestId("gallery-response");
  await expect(sent).toContainText('"type": "team_response"');
  await expect(sent).toContainText('"connectors": [\n        "github"');
  expect(calls).toEqual([]);
});

test("the same state renders as the parked Inbox item and resolves with the full decision", async ({ page }) => {
  await page.goto("/#/gallery/team-request/beyond-usual-set?place=inbox");
  await expect(page.getByTestId("inbox-item-gallery-item")).toBeVisible();
  await expect(page.getByTestId("teamreq-beyond-0")).toContainText("you asked");
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByTestId("gallery-response")).toContainText("/v1/inbox/gallery-item/resolve");
  await expect(page.getByTestId("gallery-response")).toContainText('"enable_chat": true');
});

test("the rail switches cards and lands on the card's first state", async ({ page }) => {
  await page.goto("/#/gallery");
  await page.getByTestId("gallery-card-work-items").click();
  await expect(page).toHaveURL(/#\/gallery\/work-items\/launch-plan$/);
  await expect(page.getByTestId("itemsreq-card")).toBeVisible();
});

test("a scenario opens the real app on a saved moment; answers are printed, not sent", async ({ page }) => {
  await page.goto("/#/gallery/scenarios");
  await page.getByTestId("gallery-scenario-lead-staffing-card-waiting").click();
  // The real session view: the mention that woke the lead, its steps, and the parked
  // staffing card answered in context — all from one JSON file.
  // (The mention card swaps names for ids under the mouse, so assert on the message itself.)
  await expect(page.getByText(/Add a PDF download to every invoice/).first()).toBeVisible();
  await expect(page.getByTestId("teamreq-card")).toBeVisible();
  await expect(page.getByTestId("teamreq-connector-0-github")).toBeChecked();
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByTestId("scenario-sent-toggle")).toContainText("sent 1");
  await page.getByTestId("scenario-sent-toggle").click();
  await expect(page.getByTestId("scenario-sent")).toContainText("/v1/inbox/scn-gate-team/resolve");
  await expect(page.getByTestId("scenario-sent")).toContainText('"approved": true');
});

test("an attended scenario replays its live card and fills the rail", async ({ page }) => {
  await page.goto("/?scenario=lead-team-running-approval#/s/scn-lead-3b");
  await expect(page.getByTestId("team-update")).toBeVisible();
  await expect(page.getByRole("button", { name: "Allow once" })).toBeVisible();
  await page.getByRole("button", { name: "Allow once" }).click();
  await page.getByTestId("scenario-sent-toggle").click();
  await expect(page.getByTestId("scenario-sent")).toContainText('"decision": "once"');
});

// GALLERY_SCENARIO="id,id" takes a full-window screenshot of each scenario.
for (const id of (process.env.GALLERY_SCENARIO || "").split(",").map((s) => s.trim()).filter(Boolean)) {
  test(`screenshot scenario ${id}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/#/gallery/scenarios`);
    await page.getByTestId(`gallery-scenario-${id}`).click();
    await page.waitForTimeout(1500);
    const dir = process.env.GALLERY_SHOT_DIR || "test-results/gallery";
    await page.screenshot({ path: path.join(dir, `scenario_${id}.png`) });
  });
}

const shots = (process.env.GALLERY_SHOT || "").split(",").map((s) => s.trim()).filter(Boolean);
for (const place of ["session", "inbox"]) {
  test(`Advanced approval guidance is editable in ${place}`, async ({ page }) => {
    await page.goto(`/#/gallery/team-request/approval-guidance?place=${place}`);
    await expect(page.getByTestId("teamreq-advanced")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByLabel("Approval guidance · sam")).not.toBeVisible();
    await page.getByTestId("teamreq-advanced").click();
    await page.getByLabel("Approval guidance · sam").fill("Local tests only. No remote writes.");
    await page.getByTestId("teamreq-approve").click();
    await expect(page.getByTestId("gallery-response")).toContainText("Local tests only. No remote writes.");
    await expect(page.getByTestId("gallery-response")).toContainText("approval_guidance");
  });
}
for (const shot of shots) {
  test(`screenshot ${shot}`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.goto(`/#/gallery/${shot}`);
    if (process.env.GALLERY_THEME === "dark") await page.getByTestId("gallery-theme-dark").click();
    if (process.env.GALLERY_WIDTH === "narrow") await page.getByTestId("gallery-width-narrow").click();
    const clicks = (process.env.GALLERY_CLICK || "").split(",").map((c) => c.trim()).filter(Boolean);
    for (const id of clicks) await page.getByTestId(id).click();
    const dir = process.env.GALLERY_SHOT_DIR || "test-results/gallery";
    const name = shot.replace(/[/?=]/g, "_") + (process.env.GALLERY_THEME === "dark" ? "_dark" : "") + (clicks.length ? "_clicked" : "") + ".png";
    await page.getByTestId("gallery-stage").screenshot({ path: path.join(dir, name) });
  });
}
