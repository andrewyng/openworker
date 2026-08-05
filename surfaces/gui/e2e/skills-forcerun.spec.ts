import { test, expect } from "./fixtures";

// SKILLS-SPEC §9 journey 4 — the "/" force-run: popup pick inserts the inline `/name `
// prefix, the send carries the skill as its OWN WebSocket field (never as message text),
// and the transcript shows ONE truthful bubble with exactly what the user typed.

test("skills-forcerun: popup pick → inline /name → skill rides the frame → one bubble", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // "/" opens the popup; picking inserts the inline prefix (no chip) and keeps focus.
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("/");
  await expect(page.getByTestId("skill-popup")).toBeVisible();
  await page.getByText("/weekly-report").click();
  await expect(box).toHaveValue("/weekly-report ");

  await box.type("cover last week");
  await box.press("Enter");

  // ONE user bubble, showing the literal line the user typed — never the model-facing
  // "load this skill…" framing (§6: the _display contract).
  await expect(page.getByText("/weekly-report cover last week")).toHaveCount(1);
  await expect(page.getByText(/Use the skill/)).toHaveCount(0);

  // The fake agent echoes what actually rode the wire: text WITHOUT the prefix, and the
  // skill as its own field.
  await expect(page.getByText(/\[skill=weekly-report\]/)).toBeVisible();
  await expect(page.getByText(/Echo: cover last week/)).toBeVisible();
});
