// Obsidian connector (local vault, auth="folder"): appears in the available list,
// the add modal renders the vault-folder field (text input in browser; the native
// picker button is desktop-only), a wrong folder shows the vault error, and a real
// vault path connects with the vault name as the account identity.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
}

test("obsidian: folder field, vault validation error, then connects", async ({ page }) => {
  await openConnectors(page);

  const card = page.getByTestId("connector-obsidian");
  await expect(card).toContainText("Obsidian");
  await card.getByRole("button", { name: "Connect" }).click();

  // Vault-folder field renders with the descriptor's help copy.
  await expect(page.getByText("Vault folder", { exact: true })).toBeVisible();
  const input = page.getByPlaceholder("~/Documents/MyVault");
  await expect(input).toBeVisible();

  // A non-vault folder surfaces the honest error.
  await input.fill("/tmp/not-a-real-one");
  await page.getByRole("button", { name: "Connect", exact: true }).last().click();
  await expect(page.getByText("isn't an Obsidian vault")).toBeVisible();

  // A vault path connects; identity = vault folder name.
  await input.fill("/Users/me/Documents/MyVault");
  await page.getByRole("button", { name: "Connect", exact: true }).last().click();
  await expect(page.getByTestId("connector-obsidian")).toContainText("MyVault");
});
