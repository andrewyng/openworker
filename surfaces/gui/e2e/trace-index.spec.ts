// UX-060: the trace index. The rail's Trace section lists the session's turns by the first
// words of each message of yours; a row goes to that message and opens the trace on its
// turn, at the artifact-reading width with the same index as the panel's left column. The
// old doors — the top-bar "Session trace" button and the hover-row link — are gone; the
// footer link under each answer stays.
import { expect, type Page } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000;
const SESSION = "pinned-cowork-1";

const HISTORY = [
  { role: "user", content: "What does greet.py do? One sentence.", ts: TS, mid: "u1" },
  { role: "assistant", content: "It greets.", ts: TS + 3, mid: "a1" },
  { role: "user", content: "List the files in this repo.", ts: TS + 60, mid: "u2" },
  { role: "assistant", content: "README.md and greet.py.", ts: TS + 62, mid: "a2" },
  { role: "user", content: "What is the latest commit?", ts: TS + 120, mid: "u3" },
  { role: "assistant", content: "abc123 init.", ts: TS + 121, mid: "a3" },
];

const turn = (n: number, first: string, last: string, ms: number, warn = 0) => ({
  turn_id: `t${n}`, seq: n, kind: "user", trigger: "user", status: "done",
  started_at: (TS + (n - 1) * 60) * 1000, duration_ms: ms, first_message_id: first, last_message_id: last,
  stamp: {}, buckets: { total: ms, model: ms }, usage: { pill: 1000 * n },
  issues: warn ? { warn } : undefined, total: 1000 * n, at_least: false, warn, auto_approved: 0, asked: 0,
});
const TURNS = [turn(1, "u1", "a1", 2600, 2), turn(2, "u2", "a2", 1200), turn(3, "u3", "a3", 900)];

async function seedTrace(page: Page) {
  await seedSessionMessages(page, SESSION, HISTORY);
  await page.route(new RegExp(`/v1/sessions/${SESSION}/footers$`), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ turns: TURNS, recording: true }) }));
  await page.route(new RegExp(`/v1/sessions/${SESSION}/turns$`), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ turns: TURNS }) }));
}

test("the rail's Trace section indexes the turns and is the one way in", async ({ page }) => {
  await seedTrace(page);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // The old doors are gone; the footer link under an answer stays.
  await expect(page.getByTestId("steps-open")).toHaveCount(0);
  await expect(page.getByTestId("bubble-steps")).toHaveCount(0);
  await expect(page.getByTestId("turn-footer-steps").first()).toBeVisible();

  // Collapsed header: turns and issues. Expanded: a row per message, with its glance.
  const head = page.getByTestId("rail-toggle-trace");
  await expect(head).toContainText("3 turns");
  await expect(head).toContainText("2 issues");
  await head.click();
  await expect(page.getByTestId("trace-row-1")).toContainText("What does greet.py do?");
  await expect(page.getByTestId("trace-row-1")).toContainText("2.6s");
  await expect(page.getByTestId("trace-row-3")).toContainText("What is the latest commit?");
});

test("a row goes to its message and opens the trace on that turn, index beside it", async ({ page }) => {
  await seedTrace(page);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByTestId("rail-toggle-trace").click();
  await page.getByTestId("trace-row-2").click();

  // The panel opens on turn 2, with the index as its left column marking that turn.
  const panel = page.getByTestId("steps-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("steps-turn")).toContainText("Turn 2 of 3");
  await expect(panel.getByTestId("steps-index")).toBeVisible();
  await expect(panel.getByTestId("trace-row-2")).toHaveAttribute("aria-current", "true");

  // The open trace is wider than the 332px rail (the artifact-reading width).
  const box = await page.locator(".right-rail").boundingBox();
  expect(box?.width ?? 0).toBeGreaterThan(500);

  // The message it indexes was brought into view and briefly marked.
  await expect(page.locator("#msg-u2")).toBeInViewport();
  await expect(page.locator("#msg-u2")).toHaveClass(/msg-flash/);

  // The index inside the panel moves the shown turn too.
  await panel.getByTestId("trace-row-3").click();
  await expect(panel.getByTestId("steps-turn")).toContainText("Turn 3 of 3");
});
