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

test("Progress shows where the job is and what comes next", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();

  // The persona's job shape, with exactly one step marked current — "where am I" and "what is
  // next" in one glance, which a todo list alone never answered.
  const steps = page.getByTestId("rail-checkpoints");
  await expect(steps).toBeVisible({ timeout: 10_000 });
  await expect(steps.locator(".rail-step")).not.toHaveCount(0);
  await expect(steps.locator(".rail-step.current")).toHaveCount(1);
});

test("the SSH bridge is visible from the rail, with the config to paste", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("access-toggle").click();

  const remote = page.getByTestId("remote-access");
  await expect(remote).toContainText("Drive this machine over SSH");
  await expect(remote).toContainText("reachable");
  // Hidden until asked for: it is a wall of JSON, useful once.
  await expect(remote.locator("pre")).toHaveCount(0);
  await page.getByTestId("remote-snippet-toggle").click();
  await expect(remote.locator("pre")).toContainText("mcpServers");
});

test("a sidecar without the probe simply omits the group", async ({ page }) => {
  // Registered after the fixture's routes, so it wins: an older server answering 404.
  await page.route("**/v1/computer-use", (route) => route.fulfill({ status: 404, body: "" }));
  await page.goto("/");
  await page.getByTestId("access-toggle").click();
  await expect(page.getByRole("region", { name: "Session access" })).toBeVisible();
  await expect(page.getByTestId("computer-use")).toHaveCount(0);
});

test("budgets and context headroom are visible while the run happens", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();

  // Declared ceilings, counted from the calls that actually happened.
  const meters = page.getByTestId("rail-meters");
  await expect(meters).toBeVisible({ timeout: 10_000 });
  await expect(meters).toContainText("tool calls");
  // A meter exposes its numbers to assistive tech, not only as a bar width.
  const bar = meters.getByRole("meter", { name: /tool calls/ });
  await expect(bar).toHaveAttribute("aria-valuemax", "12");
});

test("the Progress header still says something useful when collapsed", async ({ page }) => {
  await page.goto("/");
  // A plain turn first: the fake agent reports usage on an ordinary reply, which is what the
  // context percentage needs. (Its approval path sends none — adding usage there would shift
  // the totals the usage-chip specs pin.)
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  // Collapsing must not reduce the section to two words and a chevron.
  const toggle = page.getByRole("button", { name: /^Progress/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("rail-checkpoints")).toHaveCount(0);
  await expect(toggle).toContainText("%");
});

test("checkpoint state reaches assistive tech, not just the eye", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();

  const steps = page.getByTestId("rail-checkpoints");
  await expect(steps).toBeVisible({ timeout: 10_000 });
  // Exactly one aria-current="step" — the state was previously a 7px dot's fill colour only.
  await expect(steps.locator('li[aria-current="step"]')).toHaveCount(1);
  await expect(steps.locator("li").first()).toContainText(/done|current step|skipped|not started/);
});

test("Memory lists threads a live recall touched, from the untruncated sidecar", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("recall memory");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Recalled two threads.")).toBeVisible();

  // The result preview is truncated mid-JSON, so parsing it yields nothing: the panel is only
  // correct if the engine's `display` sidecar rode through the event onto the tool item.
  const memory = page.getByRole("button", { name: /^Memory/ });
  await expect(memory).toContainText("2 read");
  await memory.click();
  const threads = page.getByTestId("rail-threads");
  await expect(threads).toContainText("openevolve-phase-2");
  await expect(threads).toContainText("local-model-reliability");
});
