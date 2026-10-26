// Machines hardening (c), 2026-09-02: a dropped session socket shows a reconnecting
// state and the client re-opens the socket by itself (ledger 2026-09-01: the v14 ECS
// rollover blanked the bridged view until the user navigated away).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a dropped session socket shows Reconnecting and recovers without navigation", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();

  await box.fill("drop the socket");
  await box.press("Enter");

  // The link is gone: the strip appears and the send path is disabled…
  const strip = page.getByTestId("session-reconnecting");
  await expect(strip).toBeVisible({ timeout: 10_000 });
  await expect(strip).toContainText(/Reconnecting/);

  // …then the client reconnects on its own (1s first retry) and the strip clears.
  await expect(strip).toHaveCount(0, { timeout: 10_000 });

  // And the session still works on the new socket.
  await box.fill("hello again");
  await box.press("Enter");
  await expect(page.getByText("Echo: hello again").first()).toBeVisible({ timeout: 10_000 });
});
