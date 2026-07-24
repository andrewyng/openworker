import { test, expect } from "./fixtures";

test("retained tool output card can page full output", async ({ page }) => {
  const ref = "out_" + "d".repeat(32);
  await page.route("**/v1/sessions/pinned-cowork-1/messages", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        messages: [
          { role: "user", content: "Inspect the build log" },
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                id: "call-output",
                function: {
                  name: "run_shell",
                  arguments: JSON.stringify({ command: "npm test" }),
                },
              },
            ],
          },
          {
            role: "tool",
            tool_call_id: "call-output",
            content: JSON.stringify({
              output_ref_version: 1,
              output_ref: ref,
              truncated: true,
              original_chars: 450,
              preview: "HEAD_SENTINEL\n[…]\nTAIL_SENTINEL",
            }),
          },
          { role: "assistant", content: "The tests passed." },
        ],
      }),
    }),
  );
  await page.goto("/");
  await page.getByText("1 step").click();
  await expect(page.getByTestId("tool-output-retained")).toBeVisible();
  await page.getByRole("button", { name: "raw" }).click();
  await page.getByRole("button", { name: "View full output" }).click();
  await expect(page.getByText(/MIDDLE_SENTINEL/)).toBeVisible();
  await expect(page.getByTestId("tool-output-complete")).toBeVisible();
});
