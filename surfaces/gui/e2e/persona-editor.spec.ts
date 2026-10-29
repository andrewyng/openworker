import { test, expect } from "./fixtures";

test("copy a built-in, configure, save, enable and try a coworker", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
  await page
    .getByRole("button", { name: "Create a copy of OpenWorker", exact: true })
    .click();
  const editor = page.getByTestId("persona-editor");
  await expect(editor.getByLabel("Name *", { exact: true })).toHaveValue(
    "OpenWorker copy",
  );
  await editor
    .getByLabel("Name *", { exact: true })
    .fill("Evidence researcher");
  await editor
    .getByLabel("Short introduction", { exact: true })
    .fill("Turn sources into clear answers");
  await editor
    .getByLabel("Instructions *", { exact: true })
    .fill("Research carefully. Link to evidence and explain uncertainty.");
  await editor.getByRole("heading", { name: "Create coworker", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("persona-editor-en.png"),
    fullPage: true,
  });
  await editor.getByRole("button", { name: "Preview capabilities" }).click();
  await expect(page.getByTestId("persona-editor-review")).toBeVisible();
  await editor.getByRole("button", { name: "Save coworker" }).click();
  await expect(page.getByTestId("persona-editor-saved")).toBeVisible();
  await expect(
    editor.getByRole("button", { name: "Try a conversation" }),
  ).toBeDisabled();
  await editor
    .getByRole("button", { name: "Enable coworker", exact: true })
    .click();
  await expect(
    editor.getByRole("button", { name: "Try a conversation" }),
  ).toBeEnabled();
  await editor.getByRole("button", { name: "Try a conversation" }).click();
  await expect(page.getByTestId("coworker-chip")).toContainText(
    "Evidence researcher",
  );
  await expect(page.getByTestId("persona-editor")).toHaveCount(0);
});

test("blank creation and custom editing preserve the identifier", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
  await page
    .getByRole("button", { name: "Create coworker", exact: true })
    .click();
  const editor = page.getByTestId("persona-editor");
  await editor.getByLabel("Name *", { exact: true }).fill("My tutor");
  await editor.getByLabel("Identifier *", { exact: true }).fill("my-tutor");
  await editor
    .getByLabel("Instructions *", { exact: true })
    .fill("Explain concepts with examples.");
  await editor.getByRole("button", { name: "Preview capabilities" }).click();
  await editor.getByRole("button", { name: "Save coworker" }).click();
  await editor.getByRole("button", { name: "Back to coworkers" }).click();
  await page
    .getByRole("button", { name: "Edit My tutor", exact: true })
    .click();
  await expect(
    editor.getByLabel("Identifier *", { exact: true }),
  ).toBeDisabled();
  await expect(
    editor.getByLabel("Instructions *", { exact: true }),
  ).toHaveValue("Explain concepts with examples.");
  await editor
    .getByLabel("Instructions *", { exact: true })
    .fill("Use short explanations and check understanding.");
  await editor.getByRole("button", { name: "Preview capabilities" }).click();
  await editor.getByRole("button", { name: "Save coworker" }).click();
  await expect(page.getByTestId("persona-editor-saved")).toContainText(
    "My tutor is saved",
  );
});

test("Chinese editor fits a narrow desktop window", async ({ page }, testInfo) => {
  await page.addInitScript(() => localStorage.setItem("openworker.lang", "zh"));
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "同事", exact: true }).click();
  await page.getByRole("button", { name: "新建角色", exact: true }).click();
  await page.getByLabel("名称 *", { exact: true }).fill("资料研究员");
  await page.getByLabel("标识符 *", { exact: true }).fill("researcher-zh");
  await page
    .getByLabel("工作说明（提示词） *", { exact: true })
    .fill("查阅资料，注明来源，并说明结论的不确定性。");
  await page.screenshot({
    path: testInfo.outputPath("persona-editor-zh.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
