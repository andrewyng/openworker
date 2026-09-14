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
          needs_key: true,
          configured: connected,
          values: {},
          suggested_models: [],
          recommended_model: null,
          fields: [
            {
              key: "api_key",
              label: "API key",
              secret: true,
              required: true,
              help: "",
              placeholder: "sk-or-…",
            },
          ],
        },
      ],
    }),
  );
  await page.route("**/v1/providers/openrouter/**", async (route) => {
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
        active: connected,
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
  test(`${surface}: OpenRouter API key precedes account login, manual login and disconnect work`, async ({
    page,
  }) => {
    await mockOpenRouter(page);
    await page.goto("/");
    await page.getByTestId("account-row").click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    if (surface === "settings") {
      await page.getByRole("button", { name: "Models", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "Run setup again" }).click();
    }
    const prefix = surface === "settings" ? "set" : "ob";
    await page.getByTestId(`${prefix}-provider-openrouter`).click();
    const key = page.getByTestId(`${prefix}-field-api_key`);
    const signIn = page.getByTestId(`${prefix}-openrouter-signin`);
    await expect(key).toBeVisible();
    await expect(signIn).toBeVisible();
    const keyBox = await key.boundingBox();
    const signInBox = await signIn.boundingBox();
    expect(keyBox!.y + keyBox!.height).toBeLessThan(signInBox!.y);
    await page
      .getByRole("button", { name: "Use a manual code", exact: true })
      .click();
    await page
      .getByLabel("Authorization code", { exact: true })
      .fill("test-authorization-code");
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(
      page.getByTestId(`${prefix}-openrouter-connected`),
    ).toContainText("account credentials active");
    await expect(key).toHaveValue("");
    await page
      .getByRole("button", { name: "Disconnect account", exact: true })
      .click();
    await expect(
      page.getByTestId(`${prefix}-openrouter-connected`),
    ).toHaveCount(0);
    await expect(signIn).toBeEnabled();
  });
}
