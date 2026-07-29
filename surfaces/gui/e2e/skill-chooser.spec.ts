import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("inline skill chips preserve exact selections through send and open SKILL.md", async ({ page }) => {
  await page.goto("/");

  const composer = page.getByPlaceholder(/Ask the coworker/);
  await composer.fill("Review this /");

  const chooser = page.getByRole("listbox", { name: "Skills" });
  await expect(chooser).toBeVisible();
  await expect(
    chooser
      .getByRole("option", { name: /pdf/i })
      .filter({ hasText: "Extract, inspect, and summarize PDF documents" }),
  ).toBeVisible();

  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-skill-chip").filter({ hasText: "pdf" })).toBeVisible();

  await composer.pressSequentially(" then /release");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-skill-chip")).toHaveCount(2);

  await page.getByTestId("composer-skill-chip").filter({ hasText: "pdf" }).click();
  await expect(page.getByText("SKILL.md", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Inspect the supplied PDF and ground every claim in its contents."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByTestId("transcript-skill-chip")).toHaveCount(2);
  await expect(
    page.getByText(/\[skills=pdf,release-notes\]$/),
  ).toBeVisible();

  await page
    .getByTestId("transcript-skill-chip")
    .filter({ hasText: "release-notes" })
    .click();
  await expect(
    page.getByText("Write concise release notes for the completed work."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Hide side panel" }).click();
  await page
    .getByTestId("transcript-skill-chip")
    .filter({ hasText: "pdf" })
    .click();
  await expect(
    page.getByText("Inspect the supplied PDF and ground every claim in its contents."),
  ).toBeVisible();
});

test("a slash selection replaces only the token at the caret", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByPlaceholder(/Ask the coworker/);
  await composer.fill("Before / after");
  for (let i = 0; i < " after".length; i += 1) {
    await composer.press("ArrowLeft");
  }

  await expect(page.getByRole("listbox", { name: "Skills" })).toBeVisible();
  await page.keyboard.press("Enter");

  const chip = page.getByTestId("composer-skill-chip").filter({ hasText: "pdf" });
  await expect(chip).toBeVisible();
  const draftText = (await composer.innerText()).replace(/\s+/g, " ");
  expect(draftText.indexOf("Before")).toBeLessThan(draftText.indexOf("pdf"));
  expect(draftText.indexOf("pdf")).toBeLessThan(draftText.indexOf("after"));
});

test("chooser keyboard navigation, Tab selection, atomic deletion, and Escape", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByPlaceholder(/Ask the coworker/);
  const chooser = page.getByRole("listbox", { name: "Skills" });

  await composer.fill("/");
  await expect(chooser).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await expect(
    chooser.getByRole("option", { name: /release-notes/i }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Tab");
  await expect(
    page.getByTestId("composer-skill-chip").filter({ hasText: "release-notes" }),
  ).toBeVisible();

  await composer.press("Backspace");
  await composer.press("Backspace");
  await expect(page.getByTestId("composer-skill-chip")).toHaveCount(0);

  await composer.pressSequentially("/");
  await expect(chooser).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(chooser).toBeHidden();
});

test("duplicate names remain disambiguated by source and exact path", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByPlaceholder(/Ask the coworker/);
  await composer.fill("/pdf");

  const options = page
    .getByRole("listbox", { name: "Skills" })
    .getByRole("option", { name: /pdf/i });
  await expect(options).toHaveCount(2);
  await expect(options.nth(0)).toContainText("shared");
  await expect(options.nth(1)).toContainText("project");

  await options.filter({ hasText: "Project-specific PDF workflow" }).click();
  const chip = page.getByTestId("composer-skill-chip").filter({ hasText: "pdf" });
  await expect(chip).toHaveAttribute(
    "title",
    /launch-note\/\.coworker\/skills\/pdf\/SKILL\.md/,
  );
  await chip.click();
  await expect(
    page.getByText("Use the launch-note project's exact PDF workflow."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("transcript-skill-chip")).toHaveCount(1);
  await expect(page.getByText(/\[skills=pdf\]$/)).toBeVisible();
});
