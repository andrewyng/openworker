// The right rail: Progress counted in the active persona's own vocabulary, and an Access
// section that tells the truth about what the session can DO — not just what it can read.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("Progress names the persona before any work has happened", async ({ page }) => {
  await page.goto("/");
  // Idle rail: the empty state says whose progress this is, rather than describing the app.
  await expect(page.getByText(/Coworker's progress appears here/)).toBeVisible();
});

test("Progress counts the turn in the persona's own terms", async ({ page }) => {
  await page.goto("/");
  // The scripted agent runs a tool when asked; "run a tool" drives the approval flow.
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();

  // Whatever the fake agent called, the rail reports it as work — never as "N tool calls".
  const activity = page.getByTestId("rail-activity");
  await expect(activity).toBeVisible({ timeout: 10_000 });
  await expect(activity).not.toContainText("tool call");
});

test("Access states whether computer use can actually run, with the fix", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("access-toggle").click();

  const group = page.getByTestId("computer-use");
  await expect(group).toBeVisible();
  await expect(group).toContainText("Browser automation");
  // The honest part: connected ≠ runnable. The browser connector reports auth:"none" as
  // connected whether or not Playwright exists.
  await expect(group).toContainText("needs setup");
  await expect(group).toContainText("pip install playwright");
  await expect(group).toContainText("python -m playwright install chromium");
});

test("when the runtime IS installed, browser use is named as live", async ({ page }) => {
  await page.route("**/v1/computer-use", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ready: true,
        playwright: true,
        browsers: true,
        detail: "Ready — agents can open pages, read them, and act with approval.",
        fix: [],
      }),
    }),
  );
  await page.goto("/");
  // The glance names it only in this state — the rule is conditional, not a blanket removal.
  await expect(page.getByTestId("access-summary")).toContainText("Browser");
  await page.getByTestId("access-toggle").click();
  const group = page.getByTestId("computer-use");
  await expect(group).toContainText("ready");
  await expect(group).not.toContainText("pip install");
});

test("a sidecar without the probe simply omits the group", async ({ page }) => {
  // Registered after the fixture's routes, so it wins: an older server answering 404.
  await page.route("**/v1/computer-use", (route) => route.fulfill({ status: 404, body: "" }));
  await page.goto("/");
  await page.getByTestId("access-toggle").click();
  await expect(page.getByRole("region", { name: "Session access" })).toBeVisible();
  await expect(page.getByTestId("computer-use")).toHaveCount(0);
});
