// Settings ▸ Models key flows on the shared provider gallery (§39 components, UX-021 page):
// bad key fails in place, a passing Test auto-saves and slides home to the gallery where the
// card wears its ✓. Providers are seeded in three states (OpenAI configured+used, Anthropic
// configured-unused, Z AI unconfigured w/ a prefilled endpoint behind the disclosure). The
// mock's /verify fails on a key containing "bad"; POST /v1/providers flips `configured`.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openModels(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
}

test("Test with a bad key fails in place; a good key saves and returns to the gallery", async ({
  page,
}) => {
  await openModels(page);
  await page.getByTestId("set-provider-zai").click();

  await page.getByTestId("set-field-api_key").fill("sk-bad-key");
  await page.getByTestId("set-test").click();
  await expect(page.getByText("Invalid API key.")).toBeVisible();

  // A good key: Test verifies AND saves (§39) — the in-field pill confirms, then the form
  // slides home and the card wears its ✓.
  await page.getByTestId("set-field-api_key").fill("sk-glm-realkey");
  await page.getByTestId("set-test").click();
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
  await expect(page.getByTestId("set-provider-zai")).toContainText("✓ Connected", {
    timeout: 5_000,
  });

  // State-restore regression (owner catch 2026-07-19): revisiting the just-saved provider
  // must show the masked placeholder + saved pill — never the typed key restored as a draft
  // (the auto-return used to stash the saved key and replay it on the next open).
  await page.getByTestId("set-provider-zai").click();
  await expect(page.getByTestId("set-field-api_key")).toHaveValue("");
  await expect(page.getByTestId("set-field-api_key")).toHaveAttribute("placeholder", "••••••••");
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
});

test("a configured provider's form opens with the saved state, no plaintext key", async ({
  page,
}) => {
  await openModels(page);
  await page.getByTestId("set-provider-openai").click();
  // Stored credentials show as the in-field saved pill + masked placeholder — never the key.
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
  await expect(page.getByTestId("set-field-api_key")).toHaveValue("");
  await expect(page.getByTestId("set-field-api_key")).toHaveAttribute("placeholder", "••••••••");
});

test("ollama endpoints: add, select, and persist a local URL", async ({ page }) => {
  // Multi-endpoint manager replaces the single base_url blur-save field. Adding an
  // endpoint selects it and mirrors the URL onto the legacy base_url for the client.
  await openModels(page);
  await page.getByTestId("set-provider-ollama").click();
  await expect(page.getByTestId("set-ollama-endpoints")).toBeVisible();

  // Fresh install may already show the add form (no endpoints yet).
  const addBtn = page.getByTestId("set-ollama-add");
  if (await addBtn.isVisible()) {
    await addBtn.click();
  }
  await page.getByTestId("set-ollama-add-label").fill("Workstation");
  await page.getByTestId("set-ollama-add-url").fill("http://127.0.0.1:9999");
  await page.getByTestId("set-ollama-add-save").click();

  await expect(page.getByTestId("set-ollama-selected-badge")).toBeVisible();
  await expect(page.getByText("Workstation")).toBeVisible();
  await expect(page.getByText("http://127.0.0.1:9999")).toBeVisible();

  // Leave and come back: endpoints survived.
  await page.getByTestId("set-back").click();
  await page.getByTestId("set-provider-ollama").click();
  await expect(page.getByText("Workstation")).toBeVisible();
  await expect(page.getByText("http://127.0.0.1:9999")).toBeVisible();
  await expect(page.getByTestId("set-ollama-selected-badge")).toBeVisible();
});
