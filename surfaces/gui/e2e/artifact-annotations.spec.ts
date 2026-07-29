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
  page.on("pageerror", (error) => {
    throw error;
  });
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
        content: [
          "<style>",
          "body { margin: 0; font-family: system-ui; }",
          ".header { padding: 24px; text-align: center; }",
          ".subtitle { display: block; margin-top: 8px; }",
          ".card { margin: 20px; padding: 20px; border-radius: 16px; background: white; }",
          ".facts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }",
          ".fact-item { padding: 12px; border-radius: 10px; background: #f8f1e8; }",
          ".fact-item strong { display: block; }",
          "</style>",
          "<main>",
          "<div class='header'><h1>Preview heading</h1><div class='subtitle'>A styled div subtitle</div></div>",
          "<div class='card'><h2>Quick Facts</h2><div class='facts-grid'>",
          "<div class='fact-item'><strong>Size</strong>107–134 cm</div>",
          "<div class='fact-item'><strong>Weight</strong>35–66 kg</div>",
          "</div></div>",
          "</main>",
        ].join(""),
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

  const frame = page.frameLocator("iframe.artifact-frame");
  const factItem = frame.locator(".fact-item").filter({ hasText: "107–134 cm" });
  await expect(factItem).toHaveCount(1);
  const factBounds = await factItem.boundingBox();
  expect(factBounds).not.toBeNull();
  await page.mouse.click(factBounds!.x + 12, factBounds!.y + 12);

  const comment = page.getByPlaceholder("Describe this change…");
  await expect(comment).toBeVisible();
  await comment.fill("Make this fact more prominent.");
  await comment.press("Enter");

  const subtitle = frame.locator(".subtitle");
  const subtitleBounds = await subtitle.boundingBox();
  expect(subtitleBounds).not.toBeNull();
  await page.mouse.click(
    subtitleBounds!.x + subtitleBounds!.width / 2,
    subtitleBounds!.y + subtitleBounds!.height / 2,
  );
  await expect(comment).toBeVisible();
  await comment.fill("Shorten this subtitle.");
  await comment.press("Enter");
  await expect(page.getByRole("button", { name: /2 comments/ })).toBeVisible();

  await page.getByRole("button", { name: "Commenting" }).click();
  await expect(overlay).toHaveCount(0);
});
