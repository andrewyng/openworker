// Real component + actual OpenShell metadata; fault injection is browser-local.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const [url, modulePath, output] = process.argv.slice(2);
assert(["127.0.0.1", "localhost"].includes(new URL(url).hostname));
const { chromium } = await import(pathToFileURL(resolve(modulePath)).href);
const browser = await chromium.launch({ headless: true });
try {
  const p = await browser.newPage({ viewport: { width: 1600, height: 1060 } }),
    errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(url);
  await p.locator(".tr-step").first().waitFor();
  await mkdir(output, { recursive: true });
  await p.locator(".tr-session").first().click();
  await p.locator(".tr-step").first().waitFor();
  const first = await p.locator(".tr-session.selected code").innerText();
  await p.locator(".tr-step").first().click();
  await p.getByRole("button", { name: "JSON", exact: true }).click();
  assert(
    (await p.locator(".tr-detail-body pre").innerText()).includes("span_id"),
  );
  await p.screenshot({
    path: resolve(output, "session-tracing-real-tree.png"),
  });
  await p.getByRole("button", { name: "Waterfall", exact: true }).click();
  assert((await p.locator(".tr-track").count()) > 0);
  await p.screenshot({
    path: resolve(output, "session-tracing-real-waterfall.png"),
  });
  const child = p.getByRole("button", { name: /Open .* session/ }).first();
  await child.waitFor();
  await child.focus();
  await p.keyboard.press("Enter");
  await p.locator('.tr-breadcrumb [data-tr="back"]').first().waitFor();
  await p.locator(".tr-step").first().waitFor();
  assert.notEqual(
    await p.locator(".tr-session.selected code").innerText(),
    first,
  );
  await p.locator(".tr-step").first().click();
  await p.screenshot({
    path: resolve(output, "session-tracing-real-child.png"),
  });
  await p.locator('.tr-breadcrumb [data-tr="back"]').first().click();
  assert.equal(await p.locator(".tr-session.selected code").innerText(), first);
  assert.equal(
    await p
      .getByRole("button", { name: "Waterfall", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  // Isolated mounted instance: paging, multiple turns, escaping, exact session match,
  // delayed content, loss of permission, and teardown. No new provider call.
  await p.evaluate(async () => {
    const { mountSessionTracing } = await import("/src/tracing.js");
    const records = await fetch("/api/v1/observability/runs?limit=25").then(
      (r) => r.json(),
    );
    const r = records.runs[0];
    const base = await fetch(
      `/api/v1/sessions/${r.session_id}/observability/${r.turn_id}/timeline?limit=100`,
    ).then((r) => r.json());
    window.requests = [];
    window.delayed = [];
    window.denied = false;
    window.moreReads = 0;
    const root = document.createElement("div");
    root.id = "isolated";
    document.body.prepend(root);
    const row = base.page.steps.find(
      (x) => x.step.operation === "model.attempt",
    ).step;
    const adapter = {
      search: async (q) => {
        window.requests.push(q);
        return {
          runs: [
            {
              session_id: "safe",
              turn_id: "t1",
              name: "<img src=x onerror=alert(1)>",
            },
          ],
          next_cursor: q.after ? null : "next",
        };
      },
      turns: async (q) => ({
        turns: q.after
          ? [{ session_id: "safe", turn_id: "t2", name: "safe" }]
          : [{ session_id: "safe", turn_id: "t1", name: "safe" }],
        next_cursor: q.after ? null : "turn-next",
      }),
      timeline: async (q) => {
        if (window.denied) throw Object.assign(Error(), { status: 403 });
        if (q.after) window.moreReads++;
        return {
          page: {
            steps: [
              {
                cursor: q.after ? 2 : 1,
                step: { ...row, span_id: q.after ? "span2" : "span1" },
              },
            ],
            through: 2,
            next: q.after ? null : 1,
          },
        };
      },
      summary: async () => ({
        summary: {
          usage: { input: { total: 10 }, output: { total: 5 } },
          coverage: {},
        },
      }),
      stepDetails: () => new Promise((r) => window.delayed.push(r)),
    };
    window.testView = mountSessionTracing(root, { adapter });
  });
  const root = p.locator("#isolated");
  await root.locator(".tr-step").waitFor();
  await root.getByRole("button", { name: "Load more steps" }).click();
  await p.waitForFunction(() => window.moreReads === 1);
  assert.equal(await root.locator(".tr-step").count(), 2);
  await root.locator(".tr-step").first().click();
  await root.locator(".tr-step").last().click();
  await p.evaluate(() => {
    window.delayed[1]({ input: "new selection" });
    window.delayed[0]({ input: "stale selection" });
  });
  await root.getByText("new selection", { exact: true }).waitFor();
  assert.equal(
    await root.getByText("stale selection", { exact: true }).count(),
    0,
  );
  await root.getByRole("button", { name: "Load more turns" }).click();
  await p.waitForFunction(
    () => document.querySelectorAll("#isolated .tr-turn").length === 2,
  );
  assert.equal(await root.locator("img").count(), 0);
  await p.evaluate(() => (window.denied = true));
  await root.getByRole("button", { name: "Refresh", exact: true }).click();
  await root
    .getByText("You cannot read this session.", { exact: true })
    .waitFor();
  assert.equal(
    await root.getByText("new selection", { exact: true }).count(),
    0,
  );
  await p.evaluate(() => window.testView.dispose());
  assert.equal(await root.locator("button").count(), 0);
  // Transport must exact-filter session IDs even if authorized search matches a prefix.
  assert.equal(
    await p.evaluate(async () => {
      const { serviceTracingAdapter } = await import("/src/tracing.js");
      const a = serviceTracingAdapter(async () => ({
        runs: [{ session_id: "a" }, { session_id: "ab" }],
        next_cursor: "more",
      }));
      const result = await a.turns({ sessionId: "a" });
      return result.turns.length;
    }),
    1,
  );
  assert.deepEqual(errors, []);
  console.log(
    "Session browser passed: real trace/child navigation, parent restore, waterfall, paging, stale detail suppression, escaping, permission loss, exact scope and disposal.",
  );
} finally {
  await browser.close();
}
