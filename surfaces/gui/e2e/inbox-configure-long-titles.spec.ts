import { test, expect } from "./fixtures";

const longTitle = "Quarterly planning and launch readiness ".repeat(6).trim();

for (const width of [1000, 1280]) {
  test(`long session titles stay inside Inbox Configure cards at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByTestId("inbox-chip").waitFor();
    await page.route("**/v1/sessions", (route) => route.fulfill({
      json: { sessions: [{
        session_id: "wp-3", title: longTitle, workspace: "", agent: "cowork",
        model: "gpt-4o", mode: "interactive", updated_at: "2026-11-01 10:00:00",
        messages: 0, pinned: false, archived: false, attention: 0, liveness: "idle",
        subscriptions: [],
      }] },
    }));
    await page.getByTestId("inbox-chip").click();
    await page.getByTestId("inbox-tab-configure").click();
    const pickers = page.getByTestId("inbox-configure").locator("select")
      .filter({ has: page.locator('option[value="wp-3"]') });
    await expect(pickers).toHaveCount(2);

    for (const picker of await pickers.all()) {
      await expect(picker).toBeVisible();
      const bounds = await picker.evaluate((el) => {
        const field = el.getBoundingClientRect();
        const card = el.closest(".rounded-xl2")!.getBoundingClientRect();
        return { left: field.left, right: field.right, cardLeft: card.left, cardRight: card.right };
      });
      expect.soft(bounds.left).toBeGreaterThanOrEqual(bounds.cardLeft);
      expect.soft(bounds.right).toBeLessThanOrEqual(bounds.cardRight);
      await picker.selectOption("wp-3");
      await expect(picker).toHaveValue("wp-3");
      await expect(picker.locator("option:checked")).toHaveText(longTitle);
    }
  });
}
