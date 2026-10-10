import { expect } from "@playwright/test";
import { test, sendSessionEvent } from "./fixtures";

const theme = process.env.TEAM_POLISH_THEME === "dark" ? "dark" : "light";
test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem("openwork-theme", value), theme);
});

test("completed, created and running disclosure rows share geometry", async ({ page }) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  await sendSessionEvent(page, { type: "turn_start", data: { input: "Prepare the Acme team" } });
  await sendSessionEvent(page, { type: "tool_proposed", data: { name: "propose_team", arguments: {} } });
  await sendSessionEvent(page, { type: "tool_finished", data: { name: "propose_team", status: "ok",
    display: { team_created: { team_id: "acme-team", workers: [{ actor: "Sam", persona: "swe-worker" }, { actor: "Maya", persona: "test-worker" }] } } } });
  await sendSessionEvent(page, { type: "tool_proposed", data: { name: "list_items", arguments: {} } });
  const headers = page.locator(".transcript > details > .transcript-disclosure");
  await expect(headers).toHaveCount(3);
  const boxes = await headers.evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect();
    const icon = node.querySelector("svg")!.getBoundingClientRect();
    const label = node.querySelector("span")!.getBoundingClientRect();
    return { x: rect.x, y: rect.y, height: rect.height, iconX: icon.x, iconCenter: icon.y + icon.height / 2,
      center: rect.y + rect.height / 2, labelX: label.x };
  }));
  for (const box of boxes) {
    expect(Math.abs(box.iconX - boxes[0].iconX)).toBeLessThan(1);
    expect(Math.abs(box.labelX - boxes[0].labelX)).toBeLessThan(1);
    expect(Math.abs(box.height - boxes[0].height)).toBeLessThan(1);
    expect(Math.abs(box.iconCenter - box.center)).toBeLessThan(1);
  }
  expect(Math.abs((boxes[1].y - boxes[0].y) - (boxes[2].y - boxes[1].y))).toBeLessThan(1);
  await page.screenshot({ path: `test-results/team-disclosures-aligned-${theme}.png` });
});

for (const width of [1440, 1000, 760]) {
  test(`sleeping strip matches the composer at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByPlaceholder(/Ask the coworker/).fill("staff the team");
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByTestId("teamreq-approve").click();
    await page.locator(".sidebar").getByText("Build the statements page").click();
    await page.setViewportSize({ width, height: 1000 });
    const strip = page.getByTestId("sleep-strip");
    await expect(strip).toBeVisible();
    const s = (await strip.boundingBox())!;
    const c = (await page.locator(".composer").boundingBox())!;
    expect(Math.abs(s.x - c.x)).toBeLessThan(1);
    expect(Math.abs(s.width - c.width)).toBeLessThan(1);
    expect(await strip.evaluate(e => e.scrollWidth <= e.clientWidth)).toBe(true);
    await page.locator(".composer-wrap").screenshot({ path: `test-results/team-sleep-${width}-${theme}.png` });
  });
}

test("lead approval shows the full command once and retains the lead explanation", async ({ page }) => {
  await page.goto("/");
  const command = "cd /workspace/acme/billing-service && npm run build";
  await sendSessionEvent(page, { type: "permission_required", data: {
    name: "decide_worker_call", arguments: { worker: "Maya", call_id: "worker-call", decision: "allow", note: "The build verifies the assigned task." },
    worker_call: { tool: "run_shell", worker: "Maya", arguments: { command, description: "Verify the build" },
      reason: `command: ${command} · description: Verify the build`, state: "pending" },
  } });
  const card = page.getByTestId("workerdec-card");
  await expect(card).toBeVisible();
  expect((await card.textContent())!.split(command)).toHaveLength(2);
  await expect(card.locator(".approval-reason")).toHaveCount(0);
  await expect(card).toContainText("The build verifies the assigned task.");
  await card.screenshot({ path: `test-results/team-worker-command-once-${theme}.png` });
});
