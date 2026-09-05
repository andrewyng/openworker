// Seeded-transcript replay (the reopen path). Everything here renders from
// GET /v1/sessions/{id}/messages via itemsFromMessages — no live turns are driven —
// which is the one path the fake agent's echo scripting could never reach: replayed
// tool calls with results, privacy-filter counts, reasoning disclosures, persisted
// notices, and connector-sourced inbound messages.
import { expect } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000; // fixed epoch — replay must not depend on "now"

const RICH_HISTORY = [
  { role: "user", content: "Audit the release branch", ts: TS },
  {
    role: "assistant",
    content: "",
    tool_calls: [
      { id: "t1", function: { name: "run_shell", arguments: JSON.stringify({ command: "git log --oneline -5" }) } },
      { id: "t2", function: { name: "read_file", arguments: JSON.stringify({ path: "CHANGELOG.md" }) } },
    ],
  },
  { role: "tool", tool_call_id: "t1", content: "abc123 release: cut 0.1.7" },
  { role: "tool", tool_call_id: "t2", content: "## 0.1.7 — fixes", _display: { hidden_by_filters: 3 } },
  {
    role: "assistant",
    content: "The branch is clean — **two checks** passed.",
    reasoning: "Compared the log against the changelog; both entries line up.",
    ts: TS + 40,
  },
  { role: "notice", kind: "compacted", text: "Context compacted" },
  { role: "assistant", content: "Anything else before I file the summary?" },
];

test("a reopened session replays rich history: tools, filters, reasoning, notices", async ({
  page,
}) => {
  await seedSessionMessages(page, "pinned-cowork-1", RICH_HISTORY);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // Plain items replay as they rendered live.
  await expect(page.getByText("Audit the release branch")).toBeVisible();
  await expect(page.locator(".md strong", { hasText: "two checks" })).toBeVisible();
  await expect(page.getByText("Context compacted")).toBeVisible();

  // The turn's tools fold into a collapsed step group; the filter count rides the summary.
  const group = page.locator(".stepgroup").first();
  await expect(group).toContainText("2 steps");
  await expect(page.getByTestId("stepgroup-hidden")).toContainText("3 hidden");

  // Expanding reveals the replayed rows with their results wired by tool_call_id.
  await group.locator("summary").click();
  await expect(page.getByTestId("turn-step")).toHaveCount(2);
  await expect(page.getByTestId("tool-hidden-count")).toBeVisible();

  // Reasoning persists as the collapsed disclosure, not live "Thinking…".
  await expect(page.getByTestId("thinking-toggle")).toContainText("Thought process");
});

test("a connector-sourced message replays as its structured card", async ({ page }) => {
  await seedSessionMessages(page, "pinned-cowork-1", [
    {
      role: "user",
      content: "[slack] Priya: Ship it when the checks are green",
      source: {
        connector: "slack",
        kind: "channel",
        channel_id: "C0REL",
        channel_name: "#release",
        sender_id: "U1",
        sender_name: "Priya",
        ts: TS,
        text: "Ship it when the checks are green",
      },
    },
    { role: "assistant", content: "Will do — watching the checks now." },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const card = page.locator(".connector-card[data-brand='slack']");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Priya");
  await expect(card).toContainText("Ship it when the checks are green");
  // The framed model-facing content must NOT double-render as a plain bubble.
  await expect(page.getByText("[slack] Priya:")).toHaveCount(0);
});

test("a replayed error notice at the tail offers Retry", async ({ page }) => {
  await seedSessionMessages(page, "pinned-cowork-1", [
    { role: "user", content: "run the report", ts: TS },
    { role: "notice", kind: "error", text: "provider unavailable" },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  await expect(page.getByText("Error: provider unavailable")).toBeVisible();
  await expect(page.getByTestId("notice-retry")).toBeVisible();
});

test("a dead MCP server replays as one quiet line with Details and Open Connectors", async ({
  page,
}) => {
  // Owner ruling 2026-08-21: never a wall of stderr in the transcript — the summary
  // names the server; the raw error hides behind Details; Open Connectors is the fix path.
  await seedSessionMessages(page, "pinned-cowork-1", [
    { role: "user", content: "hi", ts: TS },
    { role: "assistant", content: "Hello!" },
    {
      role: "notice",
      kind: "mcp_error",
      server: "sales-db",
      text: "MCP server “sales-db” failed to start: unhandled errors in a TaskGroup — aws configure export-credentials --profile aicreator exited 255",
    },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const line = page.getByTestId("mcp-notice");
  await expect(line).toContainText("sales-db");
  await expect(line).toContainText("didn’t start");
  // The raw error stays hidden until asked for.
  await expect(page.getByTestId("mcp-notice-detail")).toHaveCount(0);
  await page.getByTestId("mcp-notice-details").click();
  await expect(page.getByTestId("mcp-notice-detail")).toContainText("TaskGroup");

  // Open Connectors jumps to the Integrations surface.
  await page.getByTestId("mcp-notice-connectors").click();
  await expect(page.getByText("Connectors", { exact: true }).first()).toBeVisible();
});

test("a LEGACY mcp_error notice (pre-server-field) also collapses to the quiet line", async ({
  page,
}) => {
  // Old sessions persisted the full text + a plain "see Settings ▸ Connectors" pointer;
  // display-time parsing recovers the server name so old transcripts clean up too.
  await seedSessionMessages(page, "pinned-cowork-1", [
    { role: "user", content: "hi", ts: TS },
    {
      role: "notice",
      kind: "mcp_error",
      text: "MCP server “sales-db” failed to start: unhandled errors in a TaskGroup <function f at 0x102ab40f0> — see Settings ▸ Connectors",
    },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const line = page.getByTestId("mcp-notice");
  await expect(line).toContainText("sales-db");
  await page.getByTestId("mcp-notice-details").click();
  const detail = page.getByTestId("mcp-notice-detail");
  await expect(detail).toContainText("TaskGroup");
  // The old plain-text pointer is dropped — the button replaces it.
  await expect(detail).not.toContainText("see Settings");
});
