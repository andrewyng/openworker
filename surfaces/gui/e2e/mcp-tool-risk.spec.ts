// Per-tool MCP risk overrides (Phase 2): the tools list shows each tool's
// effective risk, and clicking a tool toggles a user-local "trust as read-only"
// override — relaxed tools run without approval prompts, and the override is
// removable in place.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openMcpTab(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await page.getByRole("button", { name: "MCP servers", exact: true }).click();
}

test("tool chips show risk and toggle a read override", async ({ page }) => {
  await openMcpTab(page);

  // Connect the curated Granola preset so a server row exists (mock flips it).
  await page.getByTestId("mcp-preset-granola").getByRole("button", { name: "Connect" }).click();
  const row = page.locator(".space-y-2 > div").filter({ hasText: "granola" }).first();
  await expect(row).toContainText("connected", { timeout: 10_000 });

  // Open the tools list: both tools ask by MCP's conservative default.
  await row.getByRole("button", { name: "tools", exact: true }).click();
  const statusChip = row.getByTestId("mcp-tool-risk-get_status");
  await expect(statusChip).toContainText("asks");
  await expect(row.getByTestId("mcp-tool-risk-set_value")).toContainText("asks");

  // Trust the read tool: chip flips to auto (runs without asking); the other stays gated.
  await statusChip.click();
  await expect(statusChip).toContainText("auto");
  await expect(row.getByTestId("mcp-tool-risk-set_value")).toContainText("asks");

  // Click again: the override is removed and approval is restored.
  await statusChip.click();
  await expect(statusChip).toContainText("asks");
});
