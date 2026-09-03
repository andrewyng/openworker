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

  // Curated OFFER renders among the Available connectors while granola isn't
  // configured (never inside Custom · MCP — a row there means a server you own).
  const preset = page.getByTestId("mcp-preset-granola");
  await expect(preset).toContainText("Granola");
  await expect(preset).toContainText("Meeting notes");

  // Connect: adds the server, starts the browser sign-in, and lands STRAIGHT on
  // the detail page (OPE-136: the connect-time tool review ceremony lives there).
  await preset.getByRole("button", { name: "Connect" }).click();
  const detail = page.getByTestId("mcp-detail-granola");
  await expect(detail).toContainText("Signing in…");

  // The status poll flips the mock to connected with its 6 tools.
  await expect(detail).toContainText("Ready", { timeout: 10_000 });
  await expect(detail).toContainText("6 tools");

  // Sign out forgets tokens; the chip needs sign-in again.
  await detail.getByTestId("mcp-signout-granola").click();
  await expect(detail).toContainText("Needs sign-in");
  await expect(detail.getByTestId("mcp-signin-granola")).toBeVisible();
});
