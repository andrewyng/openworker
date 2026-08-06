import { test, expect } from "./fixtures";

// SKILLS-SPEC §9 journey 3 — import with the mandatory review gate: the preview installs
// NOTHING; confirm installs and the row wears the `uploaded` provenance badge. Hermetic:
// stage/confirm round-trip through fixtures.ts state.

test("skills-upload: preview installs nothing → confirm → uploaded badge", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Skills", exact: true }).click();

  // Add skill ▾ → Import a file → straight to the (hidden) picker.
  await page.getByRole("button", { name: /Add skill/ }).click();
  await page.getByText("Import a file").click();
  await page.getByLabel("Upload a skill archive").setInputFiles({
    name: "greet.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PKfake"),
  });

  // The mandatory review screen: everything parsed, nothing installed yet.
  await expect(page.getByText("Review before installing")).toBeVisible();
  await expect(page.getByText("says hello")).toBeVisible();
  await expect(page.getByText("Say hello warmly.")).toBeVisible();
  await expect(page.getByText(/notes\.txt/)).toBeVisible();
  await expect(page.getByText("greet", { exact: true })).toHaveCount(1); // preview only, no row

  await page.getByRole("button", { name: "Install skill" }).click();

  // Installed: teal name-first banner, a real row with the provenance badge + folder chip.
  const status = page.getByRole("status");
  await expect(status).toContainText("greet");
  await expect(status).toContainText("can now use it in every conversation");
  await expect(page.getByText("greet", { exact: true })).toHaveCount(2); // banner + the new row
  await expect(page.getByText("uploaded")).toHaveCount(2); // html-to-markdown + greet
});
