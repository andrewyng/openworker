import { test, expect } from "./fixtures";

// Guards the Settings-as-page refactor (§13, IA per UX-021): the ⚙ menu opens a full-page
// surface with a left sub-nav — General · Models · Voice input — and each section renders.
// Files is a card inside General; Coworkers ships on (flag "0" hides it).
test("Settings opens as a full page and navigates sections", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();

  // Full-page: left sub-nav + the General section (no modal backdrop).
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  for (const label of ["General", "Models & Keys", "Voice input"]) {
    await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
  }
  // Folded tabs: Files is a General card now; Coworkers ships as its own tab (UX-029).
  await expect(page.getByRole("button", { name: "Files", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Coworkers", exact: true })).toBeVisible();

  // The Files card lives inside General.
  await expect(page.getByText("Each conversation gets its own folder")).toBeVisible();

  await page.getByRole("button", { name: "Models & Keys" }).click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

// The flag's "0" escape hatch hides the tab again (the default is on — UX-029).
test("Settings: Coworkers tab opens by default; flag \"0\" hides it", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
  await expect(page.getByTestId("install-disclosure")).toBeVisible();
});

test("Settings: the flag escape hatch hides the Coworkers tab", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ocw.flag.personas", "0"));
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Coworkers", exact: true })).toHaveCount(0);
});

// UX-021: Settings ▸ Models is the shared provider gallery (§39 components). Cards wear
// their own state (✓ Connected · used …); a vendor card opens the shared key form with the
// prefilled endpoint behind the disclosure; unconfigured providers preview their models.
test("Models: provider gallery states; vendor form previews models", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();

  // Card states from the fixtures: openai configured with models in the picker, anthropic
  // configured, zai not. UX-055: a connected card counts its picker models; connected
  // cards come first in their group, the rest sit quieter behind them; local servers group apart.
  await expect(page.getByTestId("set-provider-openai")).toContainText("✓ Connected · 4 models");
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("✓ Connected");
  await expect(page.getByTestId("set-provider-zai")).toContainText("Not set up");
  await expect(page.getByTestId("set-provider-ollama")).toContainText("No key needed");
  await expect(page.getByTestId("provider-group-local")).toContainText("Ollama");
  await expect(page.getByTestId("set-provider-zai")).not.toHaveClass(/bg-panel/);
  // Five API-key providers fit under the fold; the "Show N more" line only appears past six.
  await expect(page.getByTestId("provider-more-api_key")).toHaveCount(0);
  await expect(page.getByTestId("pick-model-btn")).toBeVisible();
  await expect(page.getByTestId("provider-search")).toBeVisible();

  // The composer-picker card lists the curated models with provider tags.
  const picker = page.getByTestId("composer-picker");
  await expect(picker).toContainText("Select what shows up for sessions");

  // Vendor form: blurb renders; the prefilled endpoint hides behind the disclosure.
  await page.getByTestId("set-provider-zai").click();
  await expect(page.getByText(/Uses Z AI's OpenAI-compatible API/)).toBeVisible();
  await page.getByTestId("set-endpoint-link").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://api.z.ai/api/paas/v4");

  // Unconfigured providers still preview their curated models (read-only, matrix labels).
  const preview = page.getByTestId("model-preview");
  await expect(preview).toContainText("Included models");
  await expect(preview).toContainText("GLM-5.2 · Z AI");

  // Back to the gallery via the crumb.
  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

test("Models: BytePlus and Volcengine Ark stay visually and operationally separate", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();

  const byteplusCard = page.getByTestId("set-provider-ark");
  const volcengineCard = page.getByTestId("set-provider-ark-agent-plan-cn");
  await expect(byteplusCard).toContainText("BytePlus Ark");
  await expect(volcengineCard).toContainText("Volcengine Ark Agent Plan");
  const byteplusLogo = await byteplusCard.locator("img").getAttribute("src");
  const volcengineLogo = await volcengineCard.locator("img").getAttribute("src");
  expect(byteplusLogo).toBeTruthy();
  expect(volcengineLogo).toBeTruthy();
  expect(byteplusLogo).not.toBe(volcengineLogo);

  await byteplusCard.click();
  await page.getByTestId("set-endpoint-link").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue(
    "https://ark.ap-southeast.bytepluses.com/api/v3",
  );
  let preview = page.getByTestId("model-preview");
  await expect(preview).toContainText("Dola Seed Evolving · BytePlus Ark");
  await expect(preview).toContainText("Dola Seed 2.1 Turbo · BytePlus Ark");
  await expect(preview).not.toContainText("Doubao Seed");

  await page.getByTestId("set-back").click();
  await volcengineCard.click();
  await page.getByTestId("set-endpoint-link").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue(
    "https://ark.cn-beijing.volces.com/api/plan/v3",
  );
  preview = page.getByTestId("model-preview");
  await expect(preview).toContainText("Doubao Seed Evolving · Volcengine Agent Plan");
  await expect(preview).toContainText("Doubao Seed 2.1 Turbo · Volcengine Agent Plan");
  await expect(preview).not.toContainText("Dola Seed");
});

// UX-021: a configured provider's form shows the in-field saved state and the Remove key…
// affordance; removing reverts the card to "Not set up".
test("Models: Remove key reverts a configured provider", async ({ page }) => {
  await page.goto("/");
  page.on("dialog", (d) => d.accept());
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Models & Keys" }).click();

  await page.getByTestId("set-provider-anthropic").click();
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
  await page.getByTestId("set-remove-key").click();

  // Back on the gallery, the card has forgotten its key.
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("Not set up");
});

// Token savings (owner ask 2026-07-17; now under Settings ▸ Context optimization,
// owner 2026-08-21): the card renders with the PDF fallback segmented control +
// attach thresholds, and edits POST through.
test("Settings: Token savings card edits PDF fallback and thresholds", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Context optimization", exact: true }).click();

  const card = page.getByTestId("token-savings-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("Token savings")).toBeVisible();

  // Fallback mode: fixture says "text"; switching marks "Send page images" active.
  const seg = page.getByTestId("pdf-fallback");
  await expect(seg.getByRole("button", { name: "Extract text" })).toHaveClass(/active/);
  const [req] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/v1/settings/pdf") && r.method() === "POST"),
    seg.getByRole("button", { name: "Send page images" }).click(),
  ]);
  expect(req.postDataJSON()).toEqual({ pdf_fallback: "images" });
  await expect(seg.getByRole("button", { name: "Send page images" })).toHaveClass(/active/);

  // Thresholds: fixture starts at 2 pages / 10 MB; editing pages POSTs the clamped value.
  await expect(card.getByTestId("pdf-max-pages")).toHaveValue("2");
  await expect(card.getByTestId("pdf-max-mb")).toHaveValue("10");
  const [req2] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/v1/settings/pdf") && r.method() === "POST"),
    card.getByTestId("pdf-max-pages").fill("30"),
  ]);
  expect(req2.postDataJSON()).toEqual({ pdf_max_pages: 30 });
});

for (const reject of [false, true]) {
  test(`MCP grant revoke uses its authoritative id and reports failure=${reject}`, async ({ page }) => {
    const grant = { id: "mcp:github:read_file", kind: "mcp_tool", name: "read_file", source: "mcp", source_id: "github", source_label: "GitHub" };
    let revoked = false;
    let payload: unknown;
    await page.route("**/v1/grants", route => route.fulfill({ json: { grants: revoked ? [] : [grant] } }));
    await page.route("**/v1/grants/revoke", async route => {
      payload = route.request().postDataJSON();
      revoked = !reject;
      await route.fulfill({ json: reject ? { ok: false, error: "Grant revocation denied" } : { ok: true, revoked: true } });
    });
    await page.goto("/");
    await page.getByTestId("account-row").click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Active grants", exact: true }).click();
    await page.getByRole("button", { name: "Revoke", exact: true }).click();
    await expect.poll(() => payload).toEqual({ grant_id: grant.id });
    if (reject) {
      await expect(page.getByText("Grant revocation denied")).toBeVisible();
      await expect(page.getByText("read_file", { exact: true })).toBeVisible();
    } else {
      await expect(page.getByText("read_file", { exact: true })).toHaveCount(0);
    }
  });
}
