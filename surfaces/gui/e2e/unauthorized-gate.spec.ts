// Machines hardening (d), 2026-09-02: when the sidecar rejects this window's launch
// token, the app shows a plain signed-out state instead of crashing on the error body
// (ledger 2026-09-01: dev GUI 401 wall + `undefined.includes` in App.tsx).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a 401 from the local backend renders the signed-out gate, not a crash", async ({ page }) => {
  // Registered after the fixtures' mocks, so it wins for these routes.
  await page.route(/\/v1\/(health|settings|sessions)(\?.*)?$/, (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Unauthorized" }),
    }),
  );
  await page.goto("/");
  const gate = page.getByTestId("unauthorized-gate");
  await expect(gate).toBeVisible({ timeout: 10_000 });
  await expect(gate).toContainText("Signed out of the coworker service");
  await expect(gate.getByRole("button", { name: "Reload" })).toBeVisible();
});
