// Reconnect-mid-turn (owner catch 2026-08-24, v0.2.0 walkthrough): opening a session
// whose turn is already running server-side never sees a live `turn_start`, so `running`
// must be restored from the ws `ready` payload — otherwise the Stop button and the
// "Waiting for agent" row vanish and the user cannot stop the turn.
//
// #506 regression: after switching AWAY from a running session and back, the re-opened
// session socket's `ready` (carrying server-truth `running`) must restore the Stop
// button — the composer must never fall back to Send on a live turn.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("opening a session with a live turn shows Stop and the waiting row", async ({ page }) => {
  await page.goto("/");
  // "Long audit" is below the sidebar's peek cap — expand the list first.
  await page.getByRole("button", { name: /Show more/ }).first().click();
  await page.getByTitle("Long audit").click();

  // ready carried running:true — Stop replaces Send, the waiting row spins.
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByText("Waiting for agent...")).toBeVisible();

  // An idle session still gets the plain send arrow (running:false path).
  await page.getByTitle("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("switch away and back to a running session restores Stop (regression #506)", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Show more/ }).first().click();

  // Running session: Stop replaces Send.
  await page.getByTitle("Long audit").click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  // Switch to an idle session — plain send arrow (this session is idle).
  await page.getByTitle("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);

  // Switch BACK to the still-running session: `ready` carries running:true, so Stop
  // must come back and Send must not (the turn is live and must stay interruptible).
  await page.getByTitle("Long audit").click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
});
