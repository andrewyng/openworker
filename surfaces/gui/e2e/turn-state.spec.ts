// Reconnect-mid-turn (owner catch 2026-08-24, v0.2.0 walkthrough): opening a session
// whose turn is already running server-side never sees a live `turn_start`, so `running`
// must be restored from the ws `ready` payload — otherwise the Stop button and the
// "Waiting for agent" row vanish and the user cannot stop the turn.
//
// Issue #506: switching away from a running session must keep Stop available on
// return, and switching to an idle session must not falsely show Stop during its
// pre-ready window. Both transitions go through the same selectSession codepath.
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
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("switching away and back preserves Stop on the running session (#506)", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Show more/ }).first().click();

  // Live session → Stop is shown.
  await page.getByTitle("Long audit").click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  // Switch to an idle session → Stop gone, plain Send.
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);

  // Back to the still-running session → Stop returns.
  await page.getByTitle("Long audit").click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  // Two idle sessions in a row → Stop must not show on either (no pre-ready leak).
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
  await page.getByText("Weekly plan 1").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});
