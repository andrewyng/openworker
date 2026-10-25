import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function mockOpenRouter(page: Page) {
  let connected = false;
  let authorizing = false;
  await page.addInitScript(() => {
    window.open = () => null;
  });
  await page.route("**/v1/providers", (route) =>
    route.fulfill({
      json: [
        {
          name: "openrouter",
          title: "OpenRouter",
          kind: "api_key",
          needs_key: true,
          configured: false,
          values: {},
          suggested_models: [],
          recommended_model: null,
          fields: [
            { key: "api_key", label: "API key", secret: true, required: true, help: "", placeholder: "sk-or-…" },
          ],
        },
        {
          name: "openrouter-account",
          title: "OpenRouter account",
          kind: "subscription",
          auth: "oauth",
          needs_key: false,
          configured: connected,
          signed_in: connected,
          values: {},
          suggested_models: [],
          recommended_model: null,
          fields: [],
        },
      ],
    }),
  );
  await page.route("**/v1/providers/openrouter-account/**", async (route) => {
    const action = new URL(route.request().url()).pathname.split("/").pop();
    if (action === "signin") {
      expect(route.request().postDataJSON()).toEqual({ manual: true });
      authorizing = true;
    } else if (action === "complete") {
      expect(route.request().postDataJSON()).toEqual({
        code: "test-authorization-code",
        attempt_id: "attempt-1",
      });
      connected = true;
      authorizing = false;
    } else if (action === "disconnect") {
      connected = false;
      authorizing = false;
    }
    await route.fulfill({
      json: {
        connected,
        authorizing,
        error: null,
        attempt_id: authorizing ? "attempt-1" : null,
        authorize_url: authorizing
          ? "https://openrouter.ai/auth?code_challenge=test&key_label=OpenWorker"
          : null,
      },
    });
  });
}

for (const surface of ["settings", "onboarding"] as const) {
  test(`${surface}: OpenRouter account is its own card; manual login and disconnect work`, async ({
    page,
  }) => {
    await mockOpenRouter(page);
    await page.goto("/");
    await page.getByTestId("account-row").click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    if (surface === "settings") {
      await page.getByRole("button", { name: "Models & Keys" }).click();
    } else {
      await page.getByRole("button", { name: "Run setup again" }).click();
    }
    const prefix = surface === "settings" ? "set" : "ob";
    await page.getByTestId(`${prefix}-provider-openrouter-account`).click();
    const signIn = page.getByTestId(`${prefix}-openrouter-signin`);
    await expect(signIn).toBeVisible();
    // The account card has no key field; the key lives on the API-key card.
    await expect(page.getByTestId(`${prefix}-field-api_key`)).toHaveCount(0);
    await page
      .getByRole("button", { name: "Use a manual code", exact: true })
      .click();
    await page
      .getByLabel("Authorization code", { exact: true })
      .fill("test-authorization-code");
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(
      page.getByTestId(`${prefix}-openrouter-connected`),
    ).toContainText("Connected to your OpenRouter account");
    await page
      .getByRole("button", { name: "Disconnect account", exact: true })
      .click();
    await expect(
      page.getByTestId(`${prefix}-openrouter-connected`),
    ).toHaveCount(0);
    await expect(signIn).toBeEnabled();
  });
}
