import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("create a persistent bot, delegate work and pause it", async ({ page }, testInfo) => {
  let bot: Record<string, unknown> | null = null;
  const jobs: Record<string, unknown>[] = [];
  await page.route("**/v1/bots**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let response: unknown;
    if (request.method() === "POST" && path === "/v1/bots") {
      bot = { ...request.postDataJSON(), id: "bot-research", session_id: "bot-research",
        workspace: "/bots/research", agent: "cowork", enabled: true, next_check: null };
      response = { ok: true, bot };
    } else if (request.method() === "POST") {
      jobs.push({ id: "job-1", prompt: request.postDataJSON().prompt, trigger: "delegation", state: "queued", error: null });
      response = { ok: true, job_id: "job-1" };
    } else if (request.method() === "PATCH") {
      bot = { ...bot, ...request.postDataJSON() };
      response = { ok: true, bot };
    } else if (path === "/v1/bots") {
      response = { bots: bot ? [bot] : [] };
    } else {
      response = { bot, jobs, running: false };
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(response) });
  });
  await page.goto("/");
  await page.getByTestId("nav-bots").click();
  await expect(page.getByRole("heading", { name: "Bots", exact: true })).toBeVisible();
  await testInfo.attach("bots-create", { body: await page.screenshot(), contentType: "image/png" });
  await page.getByLabel("Name", { exact: true }).fill("Researcher");
  await page.getByLabel("Role, goals and standing instructions").fill("Track research and prepare useful briefs.");
  await page.getByRole("button", { name: "Create bot", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Researcher", exact: true })).toBeVisible();
  await page.getByLabel("Delegate a task").fill("Review this week's research notes.");
  await page.getByRole("button", { name: "Add to queue" }).click();
  await expect(page.getByText("Review this week's research notes.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Pause bot" }).click();
  await expect(page.getByRole("button", { name: "Resume bot" })).toBeVisible();
  await testInfo.attach("bots-paused-queue", { body: await page.screenshot(), contentType: "image/png" });
  expect(bot?.session_id).toBe("bot-research");
  expect(jobs[0].state).toBe("queued");
});
