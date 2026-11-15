// One bounded live task. Only its exact Markdown write/read may be approved.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { repo } from "../helm/local.mjs";
const { chromium } = await import(
  process.env.OW_PLAYWRIGHT_MODULE || "playwright"
);
const root = process.env.OW_LIVE_ROOT || join(repo, ".ow-aws"),
  origin = "http://127.0.0.1:54350";
await mkdir(join(root, "taskdesk-test"), { recursive: true, mode: 0o700 });
for (let n = 0; n < 30; n++) {
  try {
    if ((await fetch(origin, { signal: AbortSignal.timeout(1000) })).ok) break;
  } catch {}
  if (n === 29) throw Error("Task Desk tunnel unavailable");
  await new Promise((r) => setTimeout(r, 1000));
}
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
let session,
  finished = false;
try {
  await page.goto(origin);
  await page.locator("#key").waitFor();
  await page
    .locator("#key")
    .fill(
      (
        await readFile(
          process.env.OW_TASKDESK_ACCESS_FILE || join(root, "access"),
          "utf8",
        )
      ).trim(),
    );
  await page.locator("#login-form button").click();
  await page.locator("#welcome").waitFor();
  await page
    .locator("#prompt")
    .fill(
      "Create a weekend packing checklist. Write packing-checklist.md with exactly five practical Markdown checklist bullets for a two-day trip. Use write_file and then read_file to verify it; do not use run_shell. Include the complete checklist in your final answer.",
    );
  await page.locator("#create-button").click();
  await page.waitForFunction(() => location.hash.length > 1);
  session = new URL(page.url()).hash.slice(1);
  const approved = new Set(),
    deadline = Date.now() + 180000;
  let snapshot;
  while (Date.now() < deadline) {
    const response = await page.request.get(
      origin + "/api/sessions/" + session,
    );
    assert.equal(response.status(), 200);
    snapshot = await response.json();
    if (snapshot.turn?.status === "completed") {
      finished = true;
      break;
    }
    assert.ok(
      !["failed", "cancelled", "interrupted", "truncated"].includes(
        snapshot.turn?.status,
      ),
      "Live task did not complete",
    );
    for (const i of snapshot.interactions) {
      if (approved.has(i.id)) continue;
      assert.equal(i.kind, "approval");
      const args = JSON.parse(i.call.arguments);
      assert.ok(
        ["write_file", "read_file"].includes(i.call.name),
        "Unexpected live tool; do not approve",
      );
      assert.equal(args.path, "packing-checklist.md");
      if (i.call.name === "write_file")
        assert.ok(
          typeof args.content === "string" && args.content.length < 4096,
        );
      assert.ok(approved.size < 2, "Too many tool calls");
      await page
        .getByRole("button", { name: "Allow once", exact: true })
        .click();
      approved.add(i.id);
    }
    await new Promise((r) => setTimeout(r, 750));
  }
  assert.ok(finished, "Live task deadline exceeded");
  assert.equal(snapshot.mode, "live");
  assert.equal(snapshot.session.model, "gpt-6-sol");
  assert.equal(approved.size, 2);
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "completed",
  );
  await page.locator("#export").click();
  await page.locator(".artifact").first().waitFor();
  const href = new URL(
    await page.locator(".artifact").first().getAttribute("href"),
    origin,
  ).href;
  const response = await page.request.get(href);
  assert.equal(response.status(), 200);
  const result = await response.text();
  assert.match(result, /pack|trip|toiletr|clothes/i);
  await page.screenshot({
    path: join(root, "taskdesk-test/live.png"),
    fullPage: true,
  });
  await page.reload();
  await page.locator(".artifact").first().waitFor();
  const restored = await page.request.get(href);
  assert.equal(await restored.text(), result);
  await writeFile(join(root, "taskdesk-test/live-result.md"), result, {
    mode: 0o600,
  });
  await writeFile(
    join(root, "taskdesk-test/live-qualification.json"),
    JSON.stringify(
      {
        time: new Date().toISOString(),
        deployment: process.env.OW_LIVE_ROOT ? "local" : "aws",
        session,
        model: snapshot.session.model,
        approvals: approved.size,
        status: snapshot.turn.status,
        artifactBytes: Buffer.byteLength(result),
        downloadAfterReload: true,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS live Sol: two explicitly bounded tool approvals, completion, S3 export and exact download after reload.",
  );
} finally {
  if (session && !finished) {
    try {
      const s = await (
        await page.request.get(origin + "/api/sessions/" + session)
      ).json();
      if (s.turn)
        await page.request.post(
          origin + "/api/sessions/" + session + "/controls",
          {
            data: {
              request_id: "live-test-stop-" + s.turn.id,
              turn_id: s.turn.id,
              action: { kind: "cancel" },
            },
          },
        );
    } catch {
      console.error(
        "Verify original live session control state before retrying.",
      );
    }
  }
  await browser.close();
}
