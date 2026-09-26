import { test, expect } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  for (const width of ["wide", "narrow"]) {
    test(`proposal cards: ${theme}, ${width}`, async ({ page }) => {
      await page.goto("/#/gallery/work-items/security-plan");
      await page.getByTestId(`gallery-theme-${theme}`).click();
      if (width === "narrow")
        await page.getByTestId("gallery-width-narrow").click();
      const card = page.getByTestId("itemsreq-card");
      await expect(card).toContainText("sandbox-1");
      await expect(card.getByTestId("proposal-external-actions")).toContainText(
        "Planned actions",
      );
      await card.locator("summary").first().click();
      await expect(card.getByText("Acceptance criteria").first()).toBeVisible();
      await expect(
        card.getByText(
          "Findings identify affected code and include reproducible evidence.",
        ),
      ).toBeVisible();
      expect(
        await card.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      ).toBe(true);
      await card.getByRole("button", { name: "Request changes" }).click();
      await card
        .getByLabel("What should change?")
        .fill("Keep scans in staging");
      await card.getByRole("button", { name: "Send feedback" }).click();
      await expect(page.getByTestId("gallery-response")).toContainText(
        '"approved": false',
      );
      await expect(page.getByTestId("gallery-response")).toContainText(
        "Keep scans in staging",
      );

      await page.goto("/#/gallery/team-request/launch-team?place=inbox");
      if (theme === "dark")
        await page.getByTestId("gallery-theme-dark").click();
      if (width === "narrow")
        await page.getByTestId("gallery-width-narrow").click();
      const team = page.getByTestId("teamreq-card");
      await team.locator("summary").first().click();
      await expect(team.getByTestId("teamreq-row-0")).toContainText(
        "Launch plan data & API",
      );
      await team.getByLabel("Worker name").first().fill("sam-custom");
      await team.getByTestId("teamreq-chat-toggle").uncheck();
      expect(
        await team.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      ).toBe(true);
      await team.getByTestId("teamreq-approve").click();
      await expect(page.getByTestId("gallery-response")).toContainText(
        "sam-custom",
      );
      await expect(page.getByTestId("gallery-response")).toContainText(
        '"enable_chat": false',
      );
    });
  }
}
