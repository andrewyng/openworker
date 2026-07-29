import { test, expect } from "./fixtures";

test("artifact comments stage a selected Markdown segment and send it with the turn", async ({
  page,
}) => {
  page.on("pageerror", (error) => {
    throw error;
  });
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
        content: [
          "# Quarterly result",
          "",
          "> Revenue grew by 18%.",
          "",
          "![Remote chart](https://example.com/chart.png)",
          "",
          "---",
          "",
          "## Breakdown",
          "",
          "| Segment | Growth |",
          "| --- | ---: |",
          "| Enterprise | 24% |",
          "| Self-serve | 11% |",
        ].join("\n"),
      },
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /report\.md/ }).click();
  await expect(page.getByText("Quarterly result", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Comment" }).click();
  const overlay = page.getByTestId("annotation-overlay");
  await expect(overlay).toHaveCSS("cursor", "crosshair");
  await expect(page.getByRole("button", { name: "Commenting" })).toHaveCSS("cursor", "pointer");
  const heading = page.getByText("Quarterly result", { exact: true });
  const bounds = await heading.boundingBox();
  expect(bounds).not.toBeNull();
  await page.mouse.click(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);

  const comment = page.getByPlaceholder("Describe this change…");
  await expect(comment).toBeVisible();
  await expect(comment).toHaveCSS("cursor", "text");
  await comment.fill("Make this heading more specific.");
  await comment.press("Enter");

  await expect(page.getByRole("button", { name: /1 comment/ })).toBeVisible();
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".message-annotation-count")).toHaveText("1 comment");
  await expect(page.getByRole("button", { name: /1 comment/ })).toHaveCount(1);

  await page.getByRole("button", { name: "Commenting" }).click();
  await expect(overlay).toHaveCount(0);
  await expect(heading).toHaveCSS("cursor", "auto");
});

test("HTML comments use the same comment cursor and restore the page cursor on exit", async ({
  page,
}) => {
  const artifact = {
    path: "output/preview.html",
    abs_path: "/tmp/openworker/output/preview.html",
    name: "preview.html",
    kind: "html",
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
        kind: "html",
        sha256: "b".repeat(64),
        content: "<main><h1>Preview heading</h1><p>Review this paragraph.</p></main>",
      },
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: /preview\.html/ }).click();
  await page.getByRole("button", { name: "Comment" }).click();

  const overlay = page
    .frameLocator("iframe.artifact-frame")
    .locator(".artifact-annotation-overlay");
  await expect(overlay).toHaveCSS("cursor", "crosshair");

  await page.getByRole("button", { name: "Commenting" }).click();
  await expect(overlay).toHaveCount(0);
});
