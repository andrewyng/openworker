// Settings ▸ Customization (UX-058): the Web search providers as radio rows with a key per
// service, and the "Also fetch pages through …" option under the active one. The fixture answers
// GET/POST /v1/web-search, PUT/DELETE /v1/web-search/keys/{name} and
// POST /v1/web-search/fetch-via-provider from per-test state.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("customization: open the tab, add a key, pick the provider, tick the fetch option", async ({ page }) => {
  await page.goto("/#/settings/customization");
  const tab = page.getByTestId("customization-tab");
  await expect(tab).toBeVisible();
  await expect(tab.getByRole("heading", { name: "Customization" })).toBeVisible();
  await expect(tab.getByText("Web search", { exact: true })).toBeVisible();

  // DuckDuckGo is in use; the keyless rows explain themselves; keyed rows say where the key is.
  await expect(page.getByTestId("websearch-in-use-duckduckgo")).toHaveText("In use");
  await expect(page.getByTestId("websearch-status-duckduckgo")).toHaveText("No key. Rate-limited, fine for occasional searches.");
  await expect(page.getByTestId("websearch-status-tavily")).toHaveText("Key saved");
  await expect(page.getByTestId("websearch-status-brave")).toHaveText("Key from environment BRAVE_API_KEY");
  await expect(page.getByTestId("websearch-key-button-tavily")).toHaveText("Update key");
  await expect(page.getByTestId("websearch-key-button-exa")).toHaveText("Add key");
  // Exa has no key, so it cannot be chosen yet.
  await expect(page.getByTestId("websearch-radio-exa")).toBeDisabled();
  await expect(page.getByTestId("websearch-radio-you")).toBeEnabled();

  // Add a key: a wrong one is refused with the server's message, a good one is stored.
  await page.getByTestId("websearch-key-button-exa").click();
  const input = page.getByTestId("websearch-key-input");
  await expect(input).toHaveAttribute("type", "password");
  await expect(page.getByText("Checked with one search.")).toBeVisible();
  await input.fill("bad-exa-key");
  await page.getByTestId("websearch-key-save").click();
  await expect(page.getByTestId("websearch-error")).toContainText("401 Unauthorized");
  await expect(page.getByTestId("websearch-radio-exa")).toBeDisabled();
  await input.fill("exa-good-key");
  await page.getByTestId("websearch-key-save").click();
  await expect(page.getByTestId("websearch-key-input")).toHaveCount(0);
  await expect(page.getByTestId("websearch-status-exa")).toHaveText("Key saved");
  await expect(page.getByTestId("websearch-key-button-exa")).toHaveText("Update key");

  // Now it can be picked; the pill moves and, since Exa reads pages, its row gains the option.
  await expect(page.getByTestId("websearch-fetch-row")).toHaveCount(0);
  await page.getByTestId("websearch-radio-exa").click();
  await expect(page.getByTestId("websearch-in-use-exa")).toHaveText("In use");
  await expect(page.getByTestId("websearch-in-use-duckduckgo")).toHaveCount(0);
  const exaRow = page.getByTestId("websearch-row-exa");
  await expect(exaRow.getByTestId("websearch-fetch-row")).toBeVisible();
  await expect(exaRow.getByText("Also fetch pages through Exa")).toBeVisible();
  await expect(exaRow.getByText("Exa then sees the address of every page the agent opens.")).toBeVisible();
  const fetchBox = page.getByTestId("websearch-fetch-toggle");
  await expect(fetchBox).not.toBeChecked();
  await fetchBox.click();
  await expect(fetchBox).toBeChecked();

  // The choice survives a reload: the fixture holds it like the server would.
  await page.reload();
  await expect(page.getByTestId("websearch-in-use-exa")).toBeVisible();
  await expect(page.getByTestId("websearch-fetch-toggle")).toBeChecked();
  // Under a provider that does not read pages the option is not shown (the flag stays stored).
  await page.getByTestId("websearch-radio-tavily").click();
  await expect(page.getByTestId("websearch-in-use-tavily")).toBeVisible();
  await expect(page.getByTestId("websearch-row-tavily").getByTestId("websearch-fetch-toggle")).toBeChecked();
  await page.getByTestId("websearch-radio-you").click();
  await expect(page.getByTestId("websearch-in-use-you")).toBeVisible();
  await expect(page.getByTestId("websearch-fetch-row")).toHaveCount(0);
});

test("customization: removing the active provider's only key falls back to DuckDuckGo", async ({ page }) => {
  await page.goto("/#/settings/customization");
  await page.getByTestId("websearch-radio-tavily").click();
  await expect(page.getByTestId("websearch-in-use-tavily")).toBeVisible();
  await page.getByTestId("websearch-key-button-tavily").click();
  await page.getByTestId("websearch-key-remove").click();
  await expect(page.getByTestId("websearch-in-use-duckduckgo")).toBeVisible();
  await expect(page.getByTestId("websearch-key-button-tavily")).toHaveText("Add key");
  await expect(page.getByTestId("websearch-radio-tavily")).toBeDisabled();
});
