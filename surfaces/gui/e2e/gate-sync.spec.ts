import { expect } from "@playwright/test";
import { test, sendSessionEvent } from "./fixtures";

for (const gate of [
  { event: "team_proposed", name: "propose_team", card: "teamreq-card", payload: { members: [{ persona: "test-worker", name: "Sam" }] } },
  { event: "items_proposed", name: "propose_work_items", card: "itemsreq-card", payload: { items: [{ title: "Verify invoices", criteria: "Tests pass" }] } },
]) {
  for (const status of ["ok", "denied"]) {
    test(`${gate.name}: external ${status} clears the live card without reload`, async ({ page }) => {
      await page.goto("/");
      await sendSessionEvent(page, { type: gate.event, data: { ...gate.payload, tool_call_id: "proposal" } });
      await expect(page.getByTestId(gate.card)).toBeVisible();
      await sendSessionEvent(page, { type: "tool_finished", data: { name: gate.name, tool_call_id: "unrelated", status } });
      await expect(page.getByTestId(gate.card)).toBeVisible();
      await sendSessionEvent(page, { type: "tool_finished", data: { name: gate.name, tool_call_id: "proposal", status } });
      await expect(page.getByTestId(gate.card)).toHaveCount(0);
      await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();
    });
  }
  test(`${gate.name}: polling repairs a missed completion event`, async ({ page }) => {
    let resolved = false;
    await page.route(/\/v1\/inbox(?:\?|$)/, route => route.fulfill({
      json: { items: resolved ? [{ id: "inbox-proposal", tool_call_id: "proposal", state: "resolved", kind: "plan",
        resolution: '{"approved":true}', data: { gate: gate.name === "propose_team" ? "team" : "items" } }] : [] },
    }));
    await page.goto("/");
    await sendSessionEvent(page, { type: gate.event, data: { ...gate.payload, tool_call_id: "proposal" } });
    await expect(page.getByTestId(gate.card)).toBeVisible();
    resolved = true;
    await expect(page.getByTestId(gate.card)).toHaveCount(0, { timeout: 7000 });
  });
}

test("an older pending Inbox response cannot resurrect a completed staffing card", async ({ page }) => {
  let release: (() => void) | undefined;
  let hold = false;
  let intercepted = false;
  await page.route(/\/v1\/inbox(?:\?|$)/, async route => {
    if (hold) {
      intercepted = true;
      await new Promise<void>(resolve => { release = resolve; });
      await route.fulfill({ json: { items: [{ id: "old-inbox", tool_call_id: "proposal", state: "pending", kind: "plan",
        title: "Create this team?", data: { gate: "team", members: [{ persona: "test-worker", name: "Sam" }] } }] } });
    } else {
      await route.fulfill({ json: { items: [] } });
    }
  });
  await page.goto("/");
  await sendSessionEvent(page, { type: "team_proposed", data: { tool_call_id: "proposal", members: [{ persona: "test-worker", name: "Sam" }] } });
  await expect(page.getByTestId("teamreq-card")).toBeVisible();
  hold = true;
  await expect.poll(() => intercepted, { timeout: 6000 }).toBe(true);
  await sendSessionEvent(page, { type: "tool_finished", data: { name: "propose_team", tool_call_id: "proposal", status: "ok" } });
  await expect(page.getByTestId("teamreq-card")).toHaveCount(0);
  hold = false;
  release?.();
  await page.waitForTimeout(200);
  await expect(page.getByTestId("teamreq-card")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create team", exact: true })).toHaveCount(0);
});
