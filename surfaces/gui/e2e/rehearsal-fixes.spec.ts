import { test, expect, sendSessionEvent } from "./fixtures";
import { launchProposal, launchTeam } from "../src/gallery/states/proposal-examples";

for (const theme of ["light", "dark"]) {
  test(`proposal totals (${theme})`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("openwork-theme", value), theme);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await sendSessionEvent(page, { type: "items_proposed", data: launchProposal });
    const work = page.getByTestId("itemsreq-card");
    await expect(work.locator(".proposal-meta")).toContainText("(7 Build · 3 Verify · 1 Accept)");
    await page.screenshot({ path: `test-results/rehearsal-work-${theme}.png` });
  });
  test(`per-worker connector selection (${theme})`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("openwork-theme", value), theme);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await sendSessionEvent(page, { type: "team_proposed", data: { ...launchTeam, other_connected: ["github"] } });
    const team = page.getByTestId("teamreq-card");
    await team.getByTestId("proposal-manage-connectors").click();
    await expect(team.getByTestId("teamreq-row-0")).toBeVisible();
    await team.getByTestId("teamreq-connector-0-github").check();
    await expect(team.getByTestId("teamreq-connector-1-github")).not.toBeChecked();
    await team.getByTestId("proposal-connector-summary").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/rehearsal-connectors-${theme}.png` });
  });
  test(`remote mode change and lead escalation (${theme})`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("openwork-theme", value), theme);
    await page.goto("/");
    await sendSessionEvent(page, { type: "mode_notice", data: { mode: "auto-approve", text: "Auto-approve is on." } });
    await expect(page.getByRole("button", { name: "Mode", exact: true })).toContainText("Auto-approve");
    await sendSessionEvent(page, { type: "permission_required", data: {
      name: "decide_worker_call", arguments: { worker: "Sam", call_id: "sample-call", decision: "allow", note: "Verify the local change." },
      worker_call: { worker: "Sam", tool: "run_shell", arguments: { command: "npm test" }, state: "pending" },
      escalation: { kind: "reviewer_unsure", reason: "The command's target needs your confirmation." },
    } });
    await expect(page.getByTestId("approval-escalation")).toContainText("Auto-approve needs your judgment");
    await page.getByTestId("workerdec-card").screenshot({ path: `test-results/rehearsal-escalation-${theme}.png` });
  });
  test(`unattributed request remains visible (${theme})`, async ({ page }) => {
    await page.goto("/#/gallery/team-view/worker-request");
    await page.getByTestId(`gallery-theme-${theme}`).click();
    await expect(page.getByTestId("team-view").getByRole("heading", { name: "Your answer is needed." })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sam · Run local regression tests" })).toBeVisible();
    await page.getByTestId("team-view").screenshot({ path: `test-results/rehearsal-unattributed-${theme}.png` });
  });
}
