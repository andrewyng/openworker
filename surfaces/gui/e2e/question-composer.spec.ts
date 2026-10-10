import { expect } from "@playwright/test";
import { test, sendSessionEvent } from "./fixtures";

// While an ask_user question is open, the composer answers it (owner-hit 2026-10-06):
// before, Enter did nothing, and a question with allow_text false left nowhere to type.
for (const allowText of [false, true]) {
  test(`a typed composer reply answers an open question (allow_text ${allowText})`, async ({ page }) => {
    await page.goto("/");
    await sendSessionEvent(page, { type: "turn_start", data: {} });
    await sendSessionEvent(page, {
      type: "question_requested",
      data: { question: "Which color theme?", options: ["Light", "Dark"], allow_text: allowText, multi: false },
    });
    await expect(page.getByText("Which color theme?")).toBeVisible();
    const composer = page.getByPlaceholder("Type your answer — or pick an option above");
    await expect(composer).toBeVisible();
    await composer.fill("Something warm, like sepia");
    await composer.press("Enter");
    await expect(page.getByText("Got your answer: Something warm, like sepia")).toBeVisible();
    await expect(composer).toHaveCount(0);
    await expect(page.getByPlaceholder(/Ask the coworker/)).toHaveValue("");
  });
}
