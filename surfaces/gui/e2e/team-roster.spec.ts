import { expect, test } from "@playwright/test";

for (const size of [12, 100])
  for (const theme of ["light", "dark"]) {
    test(`${size} workers: compact groups, search handoff and paging (${theme})`, async ({
      page,
    }) => {
      await page.addInitScript((theme) => {
        localStorage.setItem("coworker:rail-hidden:v1", "0");
        localStorage.setItem("openwork-theme", theme);
      }, theme);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(`/?scenario=team-view-${size}#/s/scn-team-view-${size}`);
      const rail = page.getByTestId("team-rail");
      // Both actions are available before expanding the roster.
      await expect(rail.getByTestId("rail-open-team-view")).toBeVisible();
      await expect(rail.getByTestId("team-chat-action")).toBeVisible();
      await rail.getByTestId("rail-toggle-team").click();
      await expect(rail.locator(".team-roster-group")).toHaveCount(4);
      expect(await rail.getByTestId(/^team-row-/).count()).toBeLessThan(20);
      await page.screenshot({
        path: `test-results/team-roster-${size}-${theme}.png`,
      });
      const search = rail.getByRole("searchbox");
      await search.fill("not-a-worker");
      await expect(rail.getByRole("status")).toHaveText("0 matching workers");
      await expect(rail.getByTestId(/^team-row-/)).toHaveCount(0);
      await search.fill("machine");
      await expect(rail.getByTestId(/^team-row-/)).toHaveCount(6);
      await rail
        .getByRole("button", { name: new RegExp(`View all ${size} matches`) })
        .click();
      const pane = page.getByTestId("team-view");
      await expect(pane.getByRole("tab", { name: "Workers" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(pane.getByRole("searchbox")).toHaveValue("machine");
      await expect(pane.getByTestId(/^team-worker-/)).toHaveCount(8);
      await pane.getByRole("button", { name: "Next workers" }).click();
      await expect(
        pane.getByText(`9–${Math.min(size, 16)} of ${size}`, { exact: true }),
      ).toBeVisible();
      await pane
        .getByTestId(/^team-worker-/)
        .first()
        .click();
      await expect(pane.getByTestId("team-worker-transcript")).toBeVisible();
      await pane.getByRole("button", { name: "← Team" }).click();
      await expect(pane.getByRole("searchbox")).toHaveValue("machine");
      await expect(
        pane.getByText(`9–${Math.min(size, 16)} of ${size}`, { exact: true }),
      ).toBeVisible();
      await pane.getByRole("button", { name: "Close team view" }).click();
      const group = rail
        .locator(".team-roster-group")
        .filter({ hasText: "swe-worker" });
      await group.locator("summary").click();
      const groupCount = size === 100 ? 40 : 5;
      await group
        .getByRole("button", { name: `View all ${groupCount} workers` })
        .click();
      await expect(pane.getByText("swe-worker", { exact: true })).toBeVisible();
      await expect(pane.getByRole("status")).toHaveText(
        `${groupCount} matching workers`,
      );
      await pane.getByRole("button", { name: "Clear filter" }).click();
      await expect(pane.getByRole("status")).toHaveText(
        `${size} matching workers`,
      );
      await expect.poll(() => page.locator(".sidebar").evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
      await page.screenshot({
        path: `test-results/team-directory-${size}-${theme}.png`,
      });
      for (const width of [1000, 760]) {
        await page.setViewportSize({width, height:1000});
        await expect(pane.getByRole("searchbox")).toBeVisible();
        expect(await pane.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      }
      await page.screenshot({path: `test-results/team-directory-${size}-${theme}-narrow.png`});
    });
  }
