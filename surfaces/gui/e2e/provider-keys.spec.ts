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

test("non-secret fields blur-save on a configured provider (ollama endpoint)", async ({
  page,
}) => {
  // Owner-hit 2026-07-23 (as the thinking-budget field, since folded into a default):
  // the Test button was the form's only save path — typing into a non-secret field and
  // leaving Settings silently discarded it. Blur now saves.
  await openModels(page);
  await page.getByTestId("set-provider-ollama").click();
  const endpoint = page.getByTestId("set-field-base_url");
  await endpoint.fill("http://127.0.0.1:9999");
  await endpoint.blur();
  await expect(page.getByTestId("set-field-saved-base_url")).toBeVisible();

  // Leave and come back: the value survived (served from the provider's stored values).
  await page.getByTestId("set-back").click();
  await page.getByTestId("set-provider-ollama").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("http://127.0.0.1:9999");
});

test("the on/off toggle disables a configured provider without touching its key", async ({
  page,
}) => {
  await openModels(page);
  await page.getByTestId("set-provider-anthropic").click();
  await expect(page.getByTestId("set-toggle-enabled")).toHaveAttribute("aria-checked", "true");

  await page.getByTestId("set-toggle-enabled").click();
  await expect(page.getByTestId("set-toggle-enabled")).toHaveAttribute("aria-checked", "false");
  // Card reflects the toggle without a re-test — the stored key is untouched.
  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("Disabled");

  // Re-enabling restores the connected state, no key re-entry required.
  await page.getByTestId("set-provider-anthropic").click();
  await page.getByTestId("set-toggle-enabled").click();
  await expect(page.getByTestId("set-toggle-enabled")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("✓ Connected");
});

test("the toggle's knob stays inset inside its track in both states", async ({ page }) => {
  // Regression (owner catch 2026-07-30): the knob had no explicit `left`, so a bare
  // `<button>`'s default `text-align: center` UA style set its static position to the
  // track's midpoint; the `translate-x` meant to slide it from edge-to-edge was added on
  // top of that instead, pushing the knob outside the track when on. jsdom (the unit-test
  // layer) can't reproduce this — it doesn't lay out real pixels — so this geometric check
  // only means something in a real browser, which is what this e2e suite runs against.
  await openModels(page);
  await page.getByTestId("set-provider-anthropic").click();
  const track = page.getByTestId("set-toggle-enabled");
  const knob = track.locator("span");

  for (const expectChecked of ["true", "false"] as const) {
    await expect(track).toHaveAttribute("aria-checked", expectChecked);
    const trackBox = await track.boundingBox();
    const knobBox = await knob.boundingBox();
    if (!trackBox || !knobBox) throw new Error("toggle not rendered");
    expect(knobBox.x).toBeGreaterThanOrEqual(trackBox.x);
    expect(knobBox.x + knobBox.width).toBeLessThanOrEqual(trackBox.x + trackBox.width);
    if (expectChecked === "true") await track.click();
  }
});
