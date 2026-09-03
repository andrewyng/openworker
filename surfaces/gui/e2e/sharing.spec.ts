import { test, expect } from "./fixtures";

// Sharing v1 (OPE-7): the picker's "Import coworker…" door, the zip-import consent flow
// (trust warning first, capabilities behind a chevron, replaces-note), and per-coworker
// export from Settings ▸ Coworkers.

test("picker's Import door lands on Settings ▸ Coworkers at the Add section", async ({ page }) => {
  await page.goto("/");
  await page.getByText("New session").first().click();
  await page.getByTestId("coworker-chip").click();
  await page.getByTestId("import-coworker").click();

  // Settings ▸ Coworkers opened, with the installer disclosure auto-opened (UX-035:
  // it's collapsed by default; the Import door pops it).
  await expect(page.getByTestId("install-disclosure")).toBeVisible();
  await expect(page.getByRole("combobox")).toBeVisible();
});

test("zip import: trust warning leads, tools collapse behind a chevron, replaces-note shows", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();

  // Open the installer disclosure, pick the Bundle zip mode, feed a file through
  // the hidden input.
  await page.getByTestId("install-disclosure").click();
  await page.getByRole("combobox").selectOption("zip");
  await page.getByTestId("persona-zip-input").setInputFiles({
    name: "team-sec.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("fake-zip-bytes"),
  });

  const review = page.getByTestId("consent-review");
  await expect(review).toBeVisible();
  // The trust warning comes FIRST (owner design).
  await expect(review.getByText(/Only enable coworkers from someone you trust/)).toBeVisible();

  const card = page.getByTestId("consent-team-sec");
  await expect(card.getByText("Team Security Coworker").first()).toBeVisible();
  await expect(card.getByText(/Can read files, create & edit files and run shell commands/)).toBeVisible();

  // Exact tools hidden until the chevron is clicked.
  await expect(card.getByText("code_files · search · shell")).toHaveCount(0);
  await card.getByTestId("consent-tools-toggle").click();
  await expect(card.getByText("code_files · search · shell")).toBeVisible();

  // Version + replaces + grew-capabilities re-consent note; recommended connector shown.
  await expect(card.getByTestId("replaces-note")).toContainText("Replaces Team Security Coworker v1");
  await expect(card.getByTestId("replaces-note")).toContainText("MORE capabilities");
  await expect(card.getByText(/github.*(recommended).*open fix PRs/)).toBeVisible();

  // Imported coworker landed disabled in the list above, pending consent —
  // and the card itself carries the Enable action (no hunting back up the list).
  const row = page.locator(".divide-y > div").filter({ hasText: "Team Security Coworker" });
  await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  await card.getByTestId("consent-enable-team-sec").click();
  await expect(card.getByTestId("consent-enabled")).toContainText("it's in your coworker picker");
  await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "true");
});

test("Export… zips an installed coworker's bundle to a chosen folder", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();

  // Export moved to the coworker detail page (UX-035); the native folder pick is
  // server-mocked → /tmp/picked-folder.
  await page.getByTestId("persona-configure-acme-notes").click();
  await page.getByTestId("persona-export").click();
  await expect(page.getByText("Exported to /tmp/picked-folder/acme-notes-coworker-v1.zip")).toBeVisible();
});
