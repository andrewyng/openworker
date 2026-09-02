// MCP OAuth quick-add (first server: Granola): the Custom · MCP group on the
// Connectors page offers a curated Connect card; connecting adds the server, kicks
// off the browser sign-in (Signing in…), and the poll flips the row to Live.
// Sign out (detail page) returns it to Needs sign-in.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
}

test("granola: quick-add card → sign-in flow → Live → sign out", async ({ page }) => {
  await openConnectors(page);

  // Curated card renders in the Custom · MCP group while granola isn't configured.
  const preset = page.getByTestId("mcp-preset-granola");
  await expect(preset).toContainText("Granola");
  await expect(preset).toContainText("Meeting notes");

  // Connect: adds the server with OAuth pending and starts the browser flow.
  await preset.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByTestId("mcp-preset-granola")).toHaveCount(0);
  const row = page.getByTestId("mcp-row-granola");
  await expect(row).toContainText("Signing in…");

  // The status poll flips the mock to connected with its 6 tools.
  await expect(row).toContainText("Live", { timeout: 10_000 });
  await expect(row).toContainText("6 tools");

  // Sign out on the detail page forgets tokens; the chip needs sign-in again.
  await row.click();
  const detail = page.getByTestId("mcp-detail-granola");
  await detail.getByTestId("mcp-signout-granola").click();
  await expect(detail).toContainText("Needs sign-in");
  await expect(detail.getByTestId("mcp-signin-granola")).toBeVisible();
});
