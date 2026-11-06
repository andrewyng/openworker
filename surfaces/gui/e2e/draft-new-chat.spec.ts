import { test, expect } from "./fixtures";

// A brand-new chat has no sidebar row to come back to, so its unsent message lives in one
// shared new-chat draft slot: it survives re-targeting the chat in the setup row, switching
// to another chat and back through "New session", and is dropped once actually sent.
test("unsent message in a brand-new chat survives setup picks and chat switches", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("New session").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("idea for a new chat");

  // Picking a folder re-targets the new chat (fresh session id) — the text stays put.
  await page.getByTestId("folder-chip").click();
  await page.locator(".setup-menu").getByRole("button", { name: /Choose a(nother)? folder…/ }).click();
  await expect(page.getByTestId("folder-chip")).toContainText("picked-folder");
  await expect(box).toHaveValue("idea for a new chat");

  // An existing chat has its own (empty) draft; "New session" brings the idea back.
  await page.getByText("Draft the launch note").first().click();
  await expect(box).toHaveValue("");
  await page.getByText("New session").first().click();
  await expect(box).toHaveValue("idea for a new chat");

  // Once sent, the next new chat starts empty.
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: idea for a new chat/)).toBeVisible();
  await page.getByText("New session").first().click();
  await expect(box).toHaveValue("");
});
