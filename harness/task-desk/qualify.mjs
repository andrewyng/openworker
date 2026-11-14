// Optional real-browser qualification against the dedicated local Helm fixture.
// OW_PLAYWRIGHT_MODULE points to an installed Playwright index.mjs; no mock agent.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
const { chromium } = await import(
  process.env.OW_PLAYWRIGHT_MODULE || "playwright"
);
const root = new URL(
  process.env.OW_TASKDESK_TEST_ROOT || "../../.ow-helm/task-desk/",
  import.meta.url,
);
const config = JSON.parse(await readFile(new URL("config.json", root), "utf8"));
const key = (await readFile(config.access_key_file, "utf8")).trim();
for (let n = 0; n < 30; n++) {
  try {
    const response = await fetch("http://127.0.0.1:" + config.port, {
      signal: AbortSignal.timeout(1000),
    });
    if (response.ok) break;
  } catch {}
  if (n === 29) throw Error("Task Desk preview did not become reachable");
  await new Promise((r) => setTimeout(r, 1000));
}
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const checks = [];
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
try {
  await page.goto("http://127.0.0.1:" + config.port);
  await page.locator("#key").fill(key);
  await page.locator("#login-form button").click();
  await page.locator("#welcome").waitFor({ state: "visible" });
  await page.screenshot({
    path: new URL("home.png", root).pathname,
    fullPage: true,
  });
  pass("Private sign-in, task directory and desktop home render");
  const prompt =
    "TEST_RESOURCE_GRANTS " +
    JSON.stringify([
      {
        name: "write_file",
        arguments: {
          path: "taskdesk-" + randomUUID() + ".txt",
          content: "Task Desk browser qualification: real sandbox write.",
        },
      },
    ]);
  await page.locator("#prompt").fill(prompt);
  const creations = [];
  let loseFirst = true;
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    creations.push(route.request().postDataJSON().request_id);
    if (loseFirst) {
      loseFirst = false;
      await route.fetch();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.locator("#create-button").click();
  await page.locator("#notice").waitFor({ state: "visible" });
  await page.locator("#create-button").click();
  await page
    .getByRole("button", { name: "Allow once", exact: true })
    .waitFor({ timeout: 90000 });
  const session = new URL(page.url()).hash.slice(1);
  assert.ok(session);
  assert.equal(creations.length, 2);
  assert.equal(creations[0], creations[1]);
  pass("Lost creation response reconciles with the same durable request ID");
  await page.reload();
  await page
    .getByRole("button", { name: "Allow once", exact: true })
    .waitFor({ timeout: 30000 });
  pass(
    "Real submission reaches approval; reload restores its durable identity",
  );
  if (process.env.OW_TASKDESK_KUBERNETES) {
    const { kubectl } = await import("../../deploy/helm/local.mjs");
    const m = JSON.parse(
      await readFile(process.env.OW_TASKDESK_KUBERNETES, "utf8"),
    );
    assert.equal(m.context, "ow-taskdesk-20261011");
    const before = await (
      await page.request.get(
        new URL("/api/sessions/" + session, page.url()).href,
      )
    ).json();
    const pending = before.interactions.find((i) => !i.response);
    await kubectl(m, [
      "-n",
      "ow-app",
      "rollout",
      "restart",
      "deployment/ow-openworker-api",
      "deployment/ow-openworker-worker",
    ]);
    for (const role of ["api", "worker"])
      await kubectl(m, [
        "-n",
        "ow-app",
        "rollout",
        "status",
        "deployment/ow-openworker-" + role,
        "--timeout=120s",
      ]);
    await page.reload();
    await page
      .getByRole("button", { name: "Allow once", exact: true })
      .waitFor({ timeout: 60000 });
    const after = await (
      await page.request.get(
        new URL("/api/sessions/" + session, page.url()).href,
      )
    ).json();
    assert.deepEqual(
      after.interactions.find((i) => i.id === pending.id),
      pending,
    );
    pass(
      "Pending approval retains exact identity through replacement of all API and worker pods",
    );
  }
  await page.screenshot({
    path: new URL("approval.png", root).pathname,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "completed",
    {},
    { timeout: 90000 },
  );
  assert.ok(
    (await page.locator("#activity").innerText()).includes("tool finished"),
  );
  const result = await page.evaluate(async (sid) => {
    const snapshot = await (await fetch("/api/sessions/" + sid)).json();
    const res = await fetch("/api/sessions/" + sid + "/events?after=0", {
      signal: AbortSignal.timeout(15000),
    });
    const reader = res.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "",
      result;
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) throw Error("Incomplete replay");
        buffer += decoder.decode(next.value, { stream: true });
        let at;
        while ((at = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, at);
          buffer = buffer.slice(at + 2);
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (
            event.type === "tool_finished" &&
            event.data.name === "write_file"
          )
            result = event.data.result;
          if (event.event_id >= snapshot.cursor) return result;
        }
      }
    } finally {
      await reader.cancel();
    }
  }, session);
  assert.notEqual(result.isError, true);
  assert.equal(JSON.parse(result.content[0].text).written, true);
  pass("Allow Once resumes real OpenShell execution and streamed completion");
  await page.locator("#export").click();
  await page.locator(".artifact").first().waitFor();
  const download = await page.request.get(
    new URL(
      await page.locator(".artifact").first().getAttribute("href"),
      page.url(),
    ).href,
  );
  assert.equal(download.status(), 200);
  assert.match(await download.text(), /fixture finished/);
  await page.reload();
  await page.locator(".artifact").first().waitFor();
  pass("Completed assistant result saved in S3 and downloadable after reload");
  await page.screenshot({
    path: new URL("completed.png", root).pathname,
    fullPage: true,
  });
  await page.locator("#followup").fill(
    "TEST_RESOURCE_GRANTS " +
      JSON.stringify([
        {
          name: "ask_user",
          arguments: {
            question: "Which format would you prefer?",
            options: ["Checklist", "Paragraph"],
          },
        },
      ]),
  );
  await page.locator("#send").click();
  await page
    .getByRole("button", { name: "Checklist", exact: true })
    .waitFor({ timeout: 60000 });
  await page.reload();
  await page.getByRole("button", { name: "Checklist", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "completed",
    {},
    { timeout: 60000 },
  );
  pass("Question survives reload and resumes after the selected answer");
  // Queue a second approval, then stop it through the separate control path.
  await page
    .locator("#followup")
    .fill(prompt.replace("qualification", "second qualification"));
  await page.locator("#send").click();
  await page
    .getByRole("button", { name: "Allow once", exact: true })
    .waitFor({ timeout: 90000 });
  await page.locator("#cancel").click();
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "cancelled",
    {},
    { timeout: 30000 },
  );
  pass("Cancellation from waiting state is applied through the control path");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#new-task").click();
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: new URL("mobile.png", root).pathname,
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  pass("Mobile layout has no horizontal overflow; no browser runtime errors");
  await writeFile(
    new URL("qualification.json", root),
    JSON.stringify(
      { time: new Date().toISOString(), session, checks },
      null,
      2,
    ),
    { mode: 0o600 },
  );
} finally {
  await browser.close();
}
