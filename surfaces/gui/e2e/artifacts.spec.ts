// OPE-91: agent-authored HTML renders in the artifact viewer inside an AIRTIGHT sandbox.
// The app webview is privileged (Tauri IPC), so the report page must be null-origin
// (no parent access) and offline (no subresource exfiltration) — while inline scripts,
// the thing report interactivity needs, keep working. The fixture page actively probes
// all three properties and reports into #probe.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openReport(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  // Seventeenth pass: sections start collapsed — expand Artifacts to reach the list.
  await page.getByTestId("rail-toggle-artifacts").click();
  await page.locator(".artifact-row", { hasText: "security-review.html" }).click();
}

test("HTML artifact renders sandboxed: scripts run, parent and network stay sealed", async ({
  page,
}) => {
  await openReport(page);
  const frame = page.getByTestId("artifact-frame");
  await expect(frame).toBeVisible();
  // No allow-same-origin, ever: with srcDoc it would run the page same-origin with the
  // privileged app webview. This assertion is the regression lock for that exact flag.
  await expect(frame).toHaveAttribute("sandbox", "allow-scripts");

  const probe = page.frameLocator('[data-testid="artifact-frame"]').locator("#probe");
  await expect(probe).toContainText("script ran in sandbox"); // interactivity works
  await expect(probe).toContainText("parent blocked"); // null origin held
  await expect(probe).toContainText("network blocked"); // CSP stopped the exfil img
  await expect(page).not.toHaveTitle("ESCAPED");
});

test("HTML artifact offers Open in browser as the unsandboxed escape hatch", async ({
  page,
}) => {
  await openReport(page);
  // UX-038: the open action lives in the labeled ⋯ menu now.
  await page.getByTestId("artifact-more").click();
  await expect(page.getByTestId("artifact-open-browser")).toBeVisible();
  await expect(page.getByTestId("artifact-copy-contents")).toBeVisible();
  await expect(page.getByTestId("artifact-copy-path")).toBeVisible();
});

test("a transcript chip opens the viewer on the FIRST click even with the rail hidden", async ({
  page,
}) => {
  // Owner-hit 2026-08-15: the chip fires one event; the rail's select-listener was only
  // registered while the rail was visible, so click #1 unhid an empty rail and the
  // selection was lost — the viewer appeared only on a later click.
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("show the report");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: "Hide side panel" }).click();

  await page.getByTestId("artifact-chip").click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
});

test("Artifacts section renders for a folder-gated coworker too (universal scratch)", async ({
  page,
}) => {
  // UX-036: every session has a scratch surface, so the drawer's Artifacts section is no
  // longer cowork-only — a security session lists its scratch-side reports the same way.
  await page.goto("/");
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByRole("button", { name: /Security Coworker/ }).click();
  await page.getByPlaceholder(/Ask the coworker/).fill("audit this repo");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("send-folder-dialog").getByRole("button", { name: "Choose a folder…" }).click();
  await expect(page.getByText(/Echo: audit this repo/)).toBeVisible();

  await expect(page.getByTestId("rail-toggle-artifacts")).toBeVisible();
  await page.getByTestId("rail-toggle-artifacts").click();
  await expect(page.locator(".artifact-row", { hasText: "security-review.html" })).toBeVisible();
});

test("Show sidebar sticks while the artifact viewer is open", async ({ page }) => {
  // Owner-hit 2026-08-21: opening the viewer auto-collapses the nav (one-shot
  // courtesy), but clicking "Show sidebar" then instantly re-collapsed it — the
  // notify effect replayed "open" on a callback identity change. The user's
  // explicit toggle must win.
  await openReport(page);
  await expect(page.getByRole("button", { name: "Show sidebar" })).toBeVisible();

  await page.getByRole("button", { name: "Show sidebar" }).click();
  await page.waitForTimeout(400); // give a regression time to re-collapse
  await expect(page.getByRole("button", { name: "Show sidebar" })).toHaveCount(0);
  await expect(page.getByText("New session").first()).toBeVisible();
  // The viewer stays open too — expanding the nav is navigation, not dismissal.
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
});

test("viewer breadcrumb goes back and ✕ closes (UX-038)", async ({ page }) => {
  await openReport(page);
  // The breadcrumb parent is the back action — returns to the rail sections.
  await page.getByTestId("artifact-crumb-back").click();
  await expect(page.getByTestId("rail-toggle-artifacts")).toBeVisible();

  // Reopen (the section is still expanded from openReport), then ✕ closes the same way.
  await page.locator(".artifact-row", { hasText: "security-review.html" }).click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
  await page.getByTestId("artifact-close").click();
  await expect(page.getByTestId("rail-toggle-artifacts")).toBeVisible();
});
