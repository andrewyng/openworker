import { test, expect } from "./fixtures";

// The core loop: boot-resume into the last session, send a message over the WebSocket, and render
// the streamed reply — plus the in-session approval round-trip (permission_required suspends the
// turn until Allow/Deny goes back over the socket). The fake agent lives in fixtures.ts.

test("send → user bubble → streamed echo reply renders", async ({ page }) => {
  await page.goto("/");

  // Boot resumes the most recent session ("Draft the launch note") and connects; the composer is
  // live once the fake agent's `ready` lands.
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();

  await box.fill("hello agent");
  await page.getByRole("button", { name: "Send" }).click();

  // Local echo of the user message, then the agent's reply (delta-streamed, then finalized).
  await expect(page.getByText("hello agent", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Echo: hello agent/)).toBeVisible();
  // The message carried the composer's visible model (model-per-message contract): what the
  // user sees at send time is exactly what serves the turn.
  await expect(page.getByText("[model=anthropic:claude-opus-4-8]")).toBeVisible();
  // …and the picker STAYS actionable after the first turn (§17 rev 2026-07-22 — mid-session
  // switching shipped); the fact also reads in the topbar's facts subtitle.
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus" })).toBeVisible();
  await expect(page.getByTestId("session-subtitle")).toContainText("Claude Opus 4.8");
  // Composer cleared and re-armed for the next turn.
  await expect(box).toHaveValue("");
});

test("approval: tool request suspends the turn; Allow once resumes it", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();

  await box.fill("please run a tool");
  await page.getByRole("button", { name: "Send" }).click();

  // The approval card surfaces the tool + reason and blocks until a decision.
  await expect(page.getByText("The coworker wants to run a command.").first()).toBeVisible();
  await page.getByRole("button", { name: "Allow once" }).last().click();

  // Decision goes back over the socket; the agent finishes the tool and the turn.
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();
});

test("approval: Deny skips the tool and the agent says so", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();

  await box.fill("please run a tool");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("button", { name: "Deny" }).last()).toBeVisible();
  await page.getByRole("button", { name: "Deny" }).last().click();
  await expect(page.getByText("Understood — skipped the command.")).toBeVisible();
});

test("long user pastes clamp with a more…/less… toggle", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();

  const tail = "END-OF-PASTE-MARKER";
  const paste =
    "reply OK. " + "lorem ipsum dolor sit amet consectetur ".repeat(60) + tail; // ~2.4k chars
  await box.fill(paste);
  await page.getByRole("button", { name: "Send" }).click();

  // Clamped: the bubble shows the head but not the tail, plus the toggle.
  const more = page.getByRole("button", { name: "more…" });
  await expect(more).toBeVisible();
  const bubble = page.locator(".bubble-user").last();
  await expect(bubble).toContainText("reply OK.");
  await expect(bubble).not.toContainText(tail);

  // Expand → full text + "less…"; collapse → clamped again.
  await more.click();
  await expect(bubble).toContainText(tail);
  const less = page.getByRole("button", { name: "less…" });
  await expect(less).toBeVisible();
  await less.click();
  await expect(bubble).not.toContainText(tail);

  // Short messages never show the control.
  await expect(page.getByText("Echo:").first()).toBeVisible();
  await box.fill("short follow-up");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("short follow-up", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "more…" })).toHaveCount(1); // still only the paste's
});
