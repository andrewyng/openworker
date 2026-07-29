import { test, expect } from "./fixtures";

test("artifact comments stage a selected Markdown segment and send it with the turn", async ({
  page,
}) => {
  const artifact = {
    path: "output/report.md",
    abs_path: "/tmp/openworker/output/report.md",
    name: "report.md",
    kind: "markdown",
    size: 128,
    modified_at: 1_753_900_000,
  };
  await page.route("**/v1/sessions/*/artifacts", (route) =>
    route.fulfill({ json: { artifacts: [artifact] } }),
  );
  await page.route("**/v1/sessions/*/artifacts/read?*", (route) =>
    route.fulfill({
      json: {
        ok: true,
        path: artifact.path,
        kind: "markdown",
        sha256: "a".repeat(64),
        content: "# Quarterly result\n\nRevenue grew by 18%.",
      },
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /report\.md/ }).click();
  await expect(page.getByText("Quarterly result", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Comment" }).click();
  const heading = page.getByText("Quarterly result", { exact: true });
  const bounds = await heading.boundingBox();
  expect(bounds).not.toBeNull();
  await page.mouse.click(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);

  const comment = page.getByPlaceholder("Describe this change…");
  await expect(comment).toBeVisible();
  await comment.fill("Make this heading more specific.");
  await comment.press("Enter");

  await expect(page.getByRole("button", { name: /1 comment/ })).toBeVisible();
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".message-annotation-count")).toHaveText("1 comment");
  await expect(page.getByRole("button", { name: /1 comment/ })).toHaveCount(1);
});
