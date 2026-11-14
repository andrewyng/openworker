// The rail's glance layer: the Environment block names where the session runs, the
// Context section carries its value on the collapsed header (FB-015), and a fresh
// context payload on assistant_message updates it — including the compaction stamp.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("environment block names the machine and folder; context reads from `ready`", async ({ page }) => {
  await page.goto("/");

  const env = page.getByTestId("rail-env");
  await expect(env).toContainText("This machine");
  await expect(env).toContainText("Temporary folder");

  // Collapsed header already carries the value: 68k of a 200k window = 34%.
  await expect(page.getByTestId("ctx-summary")).toContainText("34%");

  // Expanded: the proportional breakdown plus the free remainder.
  await page.getByTestId("rail-toggle-context").click();
  const panel = page.getByTestId("ctx-panel");
  await expect(panel).toContainText("68k of 200k");
  await expect(panel).toContainText("System prompt");
  await expect(panel).toContainText("Tools");
  await expect(panel).toContainText("Conversation");
  await expect(panel).toContainText("Free");
});

test("a turn's context payload updates the meter and surfaces the compaction note", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("compact the context");
  await page.getByRole("button", { name: "Send" }).click();

  // 41k of 200k after the fold — the header value moved with the turn.
  await expect(page.getByTestId("ctx-summary")).toContainText("21%");
  await page.getByTestId("rail-toggle-context").click();
  await expect(page.getByTestId("ctx-compacted")).toContainText("12");
});

test("a spawned subagent appears in the Subagents section and settles to done", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("spawn a subagent");
  await page.getByRole("button", { name: "Send" }).click();

  const head = page.getByTestId("rail-toggle-subagents");
  // The row appears with the start event and settles to done when the background
  // finish event lands (asserting the transient "running" state would race it).
  await expect(head).toContainText("1 done");
  await head.click();
  const list = page.getByTestId("agent-tasks");
  await expect(list).toContainText("repo survey");
});

test("collapsed section headers still answer with a summary value", async ({ page }) => {
  await page.goto("/");
  // Progress reports the live turn while it runs.
  await page.getByPlaceholder(/Ask the coworker/).fill("run a tool");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("rail-toggle-progress")).toContainText("Working…");
});
