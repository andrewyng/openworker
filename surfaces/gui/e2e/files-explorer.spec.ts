// UX-037: Files — an explorer over the session's roots. Each root opens in the artifact
// viewer (breadcrumb "Files"), whose folder listings click through to subfolders and
// files. Artifacts stays the curated scratch-only surface beside it.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("Files lists the session roots and browses into a file", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  // Collapsed by default like every section (the More fold is gone — owner 2026-08-20).
  await page.getByTestId("rail-toggle-files").click();
  const row = page.getByTestId("files-root-row").first();
  await expect(row).toContainText("scratch");
  await expect(row).toContainText("read-write");

  // Root → folder listing in the viewer, breadcrumb says Files.
  await row.click();
  await expect(page.getByTestId("artifact-folder")).toBeVisible();
  await expect(page.locator(".artifact-title")).toContainText("Files");

  // Drill into a file: the same viewer renders it.
  await page.getByRole("button", { name: /notes\.md/ }).click();
  await expect(page.locator(".artifact-md")).toContainText("hello from the explorer");
});
