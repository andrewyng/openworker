// Settings ▸ Models & Keys: the permanent "Add an OpenAI-compatible provider" box at the end
// of the API-keys group (PR #224 design, owner follow-up 2026-10-26). Saving it creates a card
// named by the user; the card's models are typed or fetched from the server; removing the
// provider removes the card. The add box stays through all of it.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openModels(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
}

test("the add box makes a named card, which lists its server's models and can be removed", async ({ page }) => {
  await openModels(page);
  // The box sits in the API-keys group, visible without expanding the fold.
  const addBox = page.getByTestId("provider-grid-api_key").getByTestId("set-provider-custom-add");
  await expect(addBox).toContainText("Add an OpenAI-compatible provider");
  await addBox.click();

  await page.getByTestId("custom-provider-title").fill("Acme Gateway");
  await expect(page.getByTestId("custom-provider-route")).toHaveValue("acme-gateway");
  await page.getByTestId("custom-provider-endpoint").fill("https://llm.acme.example/v1");
  await page.getByTestId("custom-provider-key").fill("acme-test");
  await page.getByTestId("custom-provider-add").click();

  // The new card opened: the user's name on it, the endpoint shown, "Remove provider…" offered.
  await expect(page.locator("main")).toContainText("Acme Gateway");
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://llm.acme.example/v1");
  await expect(page.getByTestId("set-remove-key")).toContainText("Remove provider");

  // Fetch from server lists the ids; ticking one adds it under the route prefix.
  await page.getByTestId("custom-fetch-models").click();
  await expect(page.getByTestId("custom-fetch-note")).toContainText("2 models listed");
  const added = page.waitForRequest((r) => r.url().endsWith("/v1/settings/models/add") && r.method() === "POST");
  await page.locator(".mlist-row", { hasText: "acme-large" }).getByRole("checkbox").click();
  expect((await added).postDataJSON()).toEqual({ model: "acme-gateway:acme-large" });

  // Back in the gallery: the card wears the name, and the add box is still there.
  await page.getByTestId("set-back").click();
  const card = page.getByTestId("set-provider-acme-gateway");
  await expect(card).toContainText("Acme Gateway");
  await expect(card).toContainText("✓ Connected");
  await expect(page.getByTestId("set-provider-custom-add")).toBeVisible();

  // Remove provider… takes the card away.
  page.once("dialog", (d) => d.accept());
  await card.click();
  await page.getByTestId("set-remove-key").click();
  await expect(page.getByTestId("set-provider-acme-gateway")).toHaveCount(0);
  await expect(page.getByTestId("set-provider-custom-add")).toBeVisible();
});

test("a built-in provider's name is refused as a route prefix", async ({ page }) => {
  await openModels(page);
  await page.getByTestId("set-provider-custom-add").click();
  await page.getByTestId("custom-provider-title").fill("OpenAI");
  await page.getByTestId("custom-provider-endpoint").fill("https://proxy.example/v1");
  await page.getByTestId("custom-provider-key").fill("k");
  await page.getByTestId("custom-provider-add").click();
  await expect(page.getByTestId("custom-provider-error")).toContainText("already in use");
  // The form stays; the user picks another prefix.
  await page.getByTestId("custom-provider-route").fill("my-openai-proxy");
  await page.getByTestId("custom-provider-add").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://proxy.example/v1");
});
