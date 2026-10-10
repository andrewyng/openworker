// Context meter (OPE-42, UX-048 placement): the composer's model pill shows the model NAME;
// hovering it opens a card (model, provider, context ring + line, "Click to change model").
// The Settings switch adds the ring to the pill itself. The fake agent attaches fixed usage to
// every echo turn (input 1k / output 200 / cache_read 8k / cache_write 800), and the settings
// fixture maps the default model to a 200k context window.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const modelPill = (page: import("@playwright/test").Page) =>
  page.locator(".dd button").filter({ hasText: "Claude Opus 4.8" });

test("model pill: name only; hover card carries provider, context line and the click hint", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const pill = modelPill(page);
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText(/Claude Opus 4.8/);
  await expect(pill).not.toHaveText(/Anthropic/);
  await expect(pill).not.toHaveAttribute("title", /.+/); // the card replaces the native title
  await expect(page.getByTestId("usage-chip")).toHaveCount(0);

  // Fresh session: no usage yet.
  await pill.hover();
  const tip = page.getByTestId("dd-tip");
  await expect(tip).toBeVisible();
  await expect(tip).toContainText("Claude Opus 4.8");
  await expect(tip).toContainText("Anthropic");
  await expect(tip).toContainText("No usage yet");
  await expect(tip).toContainText("Click to change model");
  await page.mouse.move(10, 10);
  await expect(tip).toHaveCount(0);

  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible({
    timeout: 10_000,
  });

  // In-context size = prompt side of the last turn: 1k + 8k + 800 = 9.8k of 200k = 5%.
  // Session totals stay on release hold (owner call 2026-08-24): context figures only.
  await pill.hover();
  await expect(tip).toContainText("Context 5% · 9.8k of 200k");
  await expect(tip).not.toContainText(/tokens|Uncached/);
  await expect(page.getByTestId("usage-chip")).toHaveCount(0); // ring in the pill is opt-in
  await page.mouse.move(10, 10);

  // Context is a level, not a sum — a second identical turn leaves the line unchanged.
  await box.fill("again");
  await box.press("Enter");
  await expect(page.getByText("Echo: again", { exact: false }).first()).toBeVisible({
    timeout: 10_000,
  });
  await pill.hover();
  await expect(tip).toContainText("Context 5% · 9.8k of 200k");

  // Opening the menu hides the card.
  await pill.click();
  await expect(tip).toHaveCount(0);
});

test("usage resets on a new session", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  await modelPill(page).hover();
  await expect(page.getByTestId("dd-tip")).toContainText("Context 5%");
  await page.mouse.move(10, 10);

  // "＋ New session" wipes the transcript — and the usage accumulation with it.
  await page.getByRole("button", { name: /New session/ }).first().click();
  await modelPill(page).hover();
  await expect(page.getByTestId("dd-tip")).toContainText("No usage yet");
});

test("Settings switch puts the context ring in the pill", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("hello");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("usage-chip")).toHaveCount(0); // default: no ring

  // Turn the ring ON in Settings -> General.
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByTestId("context-bar-toggle")).not.toBeChecked();
  const [req] = await Promise.all([
    page.waitForRequest(
      (r) => r.url().endsWith("/v1/settings/context-bar") && r.method() === "POST",
    ),
    page.getByTestId("context-bar-toggle").check(),
  ]);
  expect(req.postDataJSON()).toEqual({ context_bar: true });

  // Reload so the app re-reads settings: the ring sits in the pill with the context label.
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByPlaceholder(/Ask the coworker/).press("Enter");
  const ring = page.getByTestId("usage-chip");
  await expect(ring).toBeVisible({ timeout: 10_000 });
  await expect(ring).toHaveAttribute("aria-label", /Context 5% · 9.8k of 200k/);
});
