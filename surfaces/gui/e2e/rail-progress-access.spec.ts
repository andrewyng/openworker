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
  // A turn that CHANGED something: Progress's glance is the plan and the work now, so it needs a
  // tool call to have anything to report. (The approval path sends no usage — which is why the
  // context percentage is asserted in the Memory test below, on a plain turn, instead.)
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();
  await expect(page.getByTestId("rail-checkpoints")).toBeVisible({ timeout: 10_000 });

  // Collapsing must not reduce the section to two words and a chevron.
  const toggle = page.getByRole("button", { name: /^Progress/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("rail-checkpoints")).toHaveCount(0);
  await expect(toggle).toContainText(/command/);
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
  // The recall itself is intake, so it is counted here too — beside the threads it touched.
  await expect(page.getByTestId("rail-memory-activity")).toContainText("1 recall");
  const threads = page.getByTestId("rail-threads");
  await expect(threads).toContainText("openevolve-phase-2");
  await expect(threads).toContainText("local-model-reliability");
});

test("context headroom stays on screen after a turn that called no tool", async ({ page }) => {
  await page.goto("/");
  // A plain conversational turn: usage, no tool call, no plan. Context fills up on
  // conversation alone, so this is the session shape where the meter matters most.
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  // Memory is collapsed by default, so its header is where the percentage has to be readable
  // without a click — a conversation-only session has no plan and no tools, and this is the
  // number that tells you to compact or start fresh.
  const memory = page.getByRole("button", { name: /^Memory/ });
  await expect(memory).toContainText("%");
  await memory.click();
  const meters = page.getByTestId("rail-context-meters");
  await expect(meters).toBeVisible();
  await expect(meters.getByRole("meter", { name: /context/ })).toHaveAttribute(
    "aria-valuemax",
    "40000",
  );
});

test("a model whose window the server cannot resolve stops the old window's meter", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();
  await page.getByRole("button", { name: /^Memory/ }).click();
  const meters = page.getByTestId("rail-context-meters");
  await expect(meters.getByRole("meter", { name: /context/ })).toHaveAttribute(
    "aria-valuemax",
    "40000",
  );

  // GPT-5.5 is outside the fixture's window matrix, so the server answers the switch with
  // context_window: null — exactly what an unloaded ollama model does on this machine.
  await page.locator(".dd").filter({ hasText: "Claude Opus 4.8" }).locator(".pill").click();
  await page.locator(".dd-item").filter({ hasText: "GPT-5.5" }).click();
  await expect(page.getByText(/Model switched to gpt-5.5/).first()).toBeVisible();

  // No window means no percentage — never the PREVIOUS model's window with this model's usage.
  // This turn called no tool and touched no thread, so the meter was the only thing Memory had:
  // the section goes with it rather than lingering as an empty header.
  await expect(page.getByTestId("rail-context-meters")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Memory/ })).toHaveCount(0);
});

test("the plan is a list, and each row says its state out loud", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Planned three steps.")).toBeVisible();

  // The rows are the panel's headline content; they carried state in colour and a line-through
  // only, so a screen reader could not tell which of them were finished.
  const plan = page.getByTestId("rail-plan");
  await expect(plan).toBeVisible();
  await expect(plan.getByRole("listitem")).toHaveCount(3);
  await expect(plan.getByRole("listitem").nth(0)).toContainText("done");
  await expect(plan.getByRole("listitem").nth(1)).toContainText("current");
  await expect(plan.getByRole("listitem").nth(2)).toContainText("not started");
  // Exactly one row is where the work is, and it says so the same way the checkpoint strip does.
  await expect(plan.locator('li[aria-current="step"]')).toHaveCount(1);
});

test('"Show all N" shows all N', async ({ page }) => {
  const files = Array.from({ length: 60 }, (_, i) => ({
    path: `file-${i}.md`,
    name: `file-${i}.md`,
    kind: "markdown",
    size: 100,
    modified_at: 1_760_000_000,
  }));
  await page.route("**/v1/sessions/*/artifacts", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ artifacts: files }) }),
  );
  await page.goto("/");

  const list = page.locator(".artifact-list");
  await expect(list.locator(".artifact-row")).toHaveCount(8);
  await page.getByRole("button", { name: "Show all 60" }).click();
  await expect(list.locator(".artifact-row")).toHaveCount(60);
  await expect(list.locator(".artifact-row").last()).toContainText("file-59.md");
});

test("native connector calls are named in the activity line, not dropped", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("work the connectors");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Pulled the connector data.")).toBeVisible();

  // Four real calls happened. The budget meter counts them, so the activity line saying
  // nothing at all is the panel contradicting itself.
  const activity = page.getByTestId("rail-activity");
  await expect(activity).toBeVisible();
  await expect(activity).toContainText("gmail search messages ×2");
  await expect(activity).toContainText("hubspot search");
  await expect(activity).toContainText("notion read page");
});

test("a second request measures itself, not everything before it", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Planned three steps.")).toBeVisible();

  const meters = page.getByTestId("rail-meters");
  const steps = page.getByTestId("rail-checkpoints");
  await expect(meters.getByRole("meter", { name: /tool calls/ })).toHaveAttribute(
    "aria-valuenow",
    "1",
  );
  await expect(steps.locator(".rail-step.done")).toHaveCount(1); // Plan, off that todo_write

  // A second request, one call of its own. A budget is "a ceiling on one kind of tool call for
  // a single run" and the strip says where THIS run has got to, so the first request's spend
  // must not still be counted and its evidence must not still be ticked.
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: /Allow once/ }).click();
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();

  await expect(meters.getByRole("meter", { name: /tool calls/ })).toHaveAttribute(
    "aria-valuenow",
    "1",
  );
  await expect(steps.locator(".rail-step.done")).toHaveCount(0);
  await expect(steps.locator('li[aria-current="step"]')).toHaveCount(1);
});

test("the plan survives reopening the conversation", async ({ page }) => {
  await page.route("**/v1/sessions/pinned-cowork-1/messages", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        messages: [
          { role: "user", content: "do the work" },
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                id: "t1",
                function: {
                  name: "todo_write",
                  arguments: JSON.stringify({
                    todos: [
                      { content: "Read the spec", status: "done" },
                      { content: "Draft the patch", status: "in_progress" },
                      { content: "Run the tests", status: "pending" },
                    ],
                  }),
                },
              },
            ],
          },
          { role: "tool", tool_call_id: "t1", content: "ok" },
          { role: "assistant", content: "Planned it." },
        ],
      }),
    }),
  );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Planned it.")).toBeVisible();

  // Compactions and threads are rebuilt from the transcript; the plan — the panel's headline
  // content and the whole point of Now/Next — was the one input that was not.
  await expect(page.getByTestId("rail-plan").getByRole("listitem")).toHaveCount(3);
  await expect(page.locator(".rail-now")).toContainText("Draft the patch");
  await expect(page.locator(".rail-next")).toContainText("Run the tests");
});

test("the Folders rows meet the rail's 12px body-copy floor", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("access-toggle").click();
  await expect(page.locator(".root-row").first()).toBeVisible();

  // The redesign's stated standard is a 12px floor, with 11px surviving only for uppercase
  // eyebrows where tracking and weight carry it. The Folders rows were never swept into it:
  // the branch/primary chip rendered at 10.5px and the access state at 11px — the two
  // smallest strings anywhere in a panel the redesign existed because it was hard to read.
  const sizes = await page
    .locator("aside.right-rail .root-tag, aside.right-rail .root-access")
    .evaluateAll((els) => els.map((el) => parseFloat(getComputedStyle(el).fontSize)));
  expect(sizes.length).toBeGreaterThan(0);
  expect(Math.min(...sizes)).toBeGreaterThanOrEqual(12);
});

test("a plan the run has left behind says so, instead of reading as live state", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan then wander");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("All three steps are finished.")).toBeVisible();

  // The turn is over and every step of it is done, but the model never rewrote the list — the
  // panel can only render what it was given. What it must NOT do is present that snapshot as
  // the present tense: "Now: Draft the patch · 1/3 done" over a finished run is what made a
  // working agent read as a stuck one, and sent the user to ask whether it was idle.
  const age = page.getByTestId("rail-plan-age");
  await expect(age).toBeVisible();
  await expect(age).toContainText("plan unchanged for 10 calls");
  await expect(page.locator(".rail-now")).toContainText("Last on");
  await expect(page.locator(".rail-now")).not.toContainText("Now");
  await expect(page.locator(".rail-next")).toContainText("Then");
});

test("a plan the model keeps current is still reported in the present tense", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Planned three steps.")).toBeVisible();

  // The other half of the rule: staleness is a threshold, not a mood. A list written this call
  // is live state, and the panel says so with no hedging and no age.
  await expect(page.getByTestId("rail-plan-age")).toHaveCount(0);
  await expect(page.locator(".rail-now")).toContainText("Now");
});
