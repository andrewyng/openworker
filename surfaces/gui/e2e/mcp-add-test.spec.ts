// UX-033/034: custom MCP servers live on the Connectors page. "Add custom server"
// (top of page) opens the two-tab modal (Remote URL / JSON); added entries land in
// the "Custom · MCP" group with honest status chips (Testing… → Live / Error /
// Needs sign-in / Not tested) and a detail subpage with Test.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
}

test("remote URL add: probe flips the row to Live with tool count", async ({ page }) => {
  await openConnectors(page);
  await page.getByTestId("add-custom-server").click();

  // URL tab is the default door; bad URL is caught before anything is added.
  const modal = page.getByTestId("add-mcp-modal");
  await modal.getByTestId("mcp-add-name").fill("notes");
  await modal.getByTestId("mcp-add-url").fill("mcp.example.com/mcp");
  await modal.getByRole("button", { name: "Add & test" }).click();
  await expect(modal.getByText("Enter the server's full URL")).toBeVisible();

  await modal.getByTestId("mcp-add-url").fill("https://mcp.example.com/mcp");
  await modal.getByRole("button", { name: "Add & test" }).click();

  const row = page.getByTestId("mcp-row-notes");
  await expect(row).toContainText("Testing…");
  await expect(row).toContainText("Live", { timeout: 10_000 });
  await expect(row).toContainText("6 tools");
});

test("guarded server: 401 → Needs sign-in chip → OAuth switch on the detail page", async ({
  page,
}) => {
  await openConnectors(page);
  await page.getByTestId("add-custom-server").click();
  const modal = page.getByTestId("add-mcp-modal");
  await modal.getByTestId("mcp-add-name").fill("locked-crm");
  await modal.getByTestId("mcp-add-url").fill("https://mcp.locked.example/mcp");
  await modal.getByRole("button", { name: "Add & test" }).click();

  // The anonymous probe 401s: the row says needs sign-in; the fix lives one
  // click deep on the detail page (with the error excerpt).
  const row = page.getByTestId("mcp-row-locked-crm");
  await expect(row).toContainText("Needs sign-in", { timeout: 10_000 });
  await row.click();

  const detail = page.getByTestId("mcp-detail-locked-crm");
  await expect(detail).toContainText("authentication required");
  await detail.getByTestId("mcp-authfix-locked-crm").click();
  await expect(detail).toContainText("Signing in…");
  await expect(detail).toContainText("Live", { timeout: 10_000 });
});

test("JSON tab adds stdio as Not tested; detail Test flips it to Live", async ({ page }) => {
  await openConnectors(page);
  await page.getByTestId("add-custom-server").click();
  const modal = page.getByTestId("add-mcp-modal");
  await modal.getByTestId("mcp-add-tab-json").click();
  await modal
    .locator("textarea")
    .fill('{"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}');
  await modal.getByRole("button", { name: "Add", exact: true }).click();

  // A pasted stdio server is configured, not connected — the chip says so.
  const row = page.getByTestId("mcp-row-files");
  await expect(row).toContainText("Not tested");
  await expect(row).toContainText("stdio");

  await row.click();
  const detail = page.getByTestId("mcp-detail-files");
  await detail.getByTestId("mcp-test-files").click();
  await expect(detail).toContainText("Testing…");
  await expect(detail).toContainText("Live", { timeout: 10_000 });
  await expect(detail).toContainText("6 tools");

  // Remove from the detail page returns to the list without the row.
  await detail.getByTestId("mcp-remove-files").click();
  await expect(page.getByTestId("mcp-row-files")).toHaveCount(0);
});

test("the name field prefills from the URL's distinctive host label", async ({ page }) => {
  await openConnectors(page);
  await page.getByTestId("add-custom-server").click();
  const modal = page.getByTestId("add-mcp-modal");

  // Generic labels (mcp/api/data/www) are skipped; the first distinctive one wins.
  await modal.getByTestId("mcp-add-url").fill("https://data.dlai.link/api/mcp");
  await expect(modal.getByTestId("mcp-add-name")).toHaveValue("dlai");

  // Never overwrite what the user typed.
  await modal.getByTestId("mcp-add-name").fill("warehouse");
  await modal.getByTestId("mcp-add-url").fill("https://mcp.linear.app/mcp");
  await expect(modal.getByTestId("mcp-add-name")).toHaveValue("warehouse");
});
