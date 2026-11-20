// Invoked by the ignored workspace_preview_ports service test, with a private gateway tunnel.
import assert from "node:assert/strict";
import https from "node:https";
import { readFile, writeFile, access } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { randomUUID } from "node:crypto";
import { Service } from "../dist/index.js";

const ca = await readFile(process.env.OW_PREVIEW_CA);
const svc = await Service.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: "test",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
const host = (slot) => new URL(slot.url).hostname;
function fetchSlot(slot, path = "/", token = slot.accessToken) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      new URL(path, process.env.OW_PREVIEW_GATEWAY),
      {
        ca,
        headers: {
          Host: host(slot),
          ...(token && { Authorization: "Bearer " + token }),
        },
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            text: Buffer.concat(chunks).toString(),
          }),
        );
      },
    );
    req.on("error", reject);
  });
}
async function call(session, name, args) {
  const turn = await session.send(
    "TEST_RESOURCE_GRANTS " + JSON.stringify([{ name, arguments: args }]),
  );
  const result = await turn.result();
  assert.equal(result.status, "completed", JSON.stringify(result));
  for await (const { event } of turn.events())
    if (event.type === "tool_finished") {
      const out = event.data.result;
      assert.equal(out.isError, false, JSON.stringify(out));
      return out.structuredContent ?? JSON.parse(out.content[0].text);
    }
  assert.fail("Missing tool_finished for " + name);
}
async function close(s) {
  for (let i = 0; ; i++)
    try {
      await s.close();
      return;
    } catch (e) {
      if (e.status !== 409 || i >= 50) throw e;
      await delay(100);
    }
}
const dir = process.env.OW_PREVIEW_BARRIER;
async function barrier(name, payload) {
  await writeFile(join(dir, name + ".json"), JSON.stringify(payload), {
    mode: 0o600,
  });
  for (let i = 0; i < 900; i++) {
    try {
      await access(join(dir, name + ".ok"));
      return;
    } catch (e) {
      if (e.code !== "ENOENT") throw e;
    }
    await delay(100);
  }
  throw new Error("Timed out at " + name);
}
const agent = await svc.agent("builder");
const key = "preview-" + randomUUID();
const write = (s, path, content) => call(s, "write_file", { path, content });
const start = (s, command) =>
  call(s, "process_start", { command, timeout_seconds: 300 });
const vite = "exec vite --host 0.0.0.0 --port 5100 --strictPort";
async function page(slot, fragment) {
  for (let i = 0; i < 40; i++) {
    const r = await fetchSlot(slot);
    if (r.status === 200 && r.text.includes(fragment)) return;
    await delay(250);
  }
  throw new Error("Preview did not serve expected page");
}
try {
  const defaults = await svc.workspaces.startProvider({ name: "websites" });
  const noSlots = await defaults.getOrCreate({ key: key + "-files" });
  assert.equal(noSlots.previews?.length ?? 0, 0);
  await defaults.stopWorkspace(noSlots.workspaceId);
  await defaults.deleteData(noSlots.dataId);
  const sites = await svc.workspaces.startProvider({
    name: "websites",
    options: {
      imageProfile: "nodejs",
      podProfile: "medium",
      previewPortCount: 5,
    },
  });
  await assert.rejects(
    sites.getOrCreate({ key: key + "-bad", options: { previewPortCount: 16 } }),
  );
  await assert.rejects(
    svc.workspaces.startProvider({
      name: "websites",
      options: { imageProfile: "unregistered" },
    }),
  );
  const a = await sites.getOrCreate({ key }),
    slots = a.previews;
  assert.ok(
    JSON.stringify(a) === JSON.stringify(await sites.getOrCreate({ key })),
    "Repeat open retains the complete slot mapping",
  );
  assert.equal(slots.length, 5);
  slots.forEach((slot, i) => {
    assert.equal(slot.port, 5100 + i);
    assert.equal(
      host(slot).split(".")[0].slice(-3),
      "-" + String(i).padStart(2, "0"),
    );
  });
  assert.equal((await fetchSlot(slots[0], "/", "")).status, 403);
  assert.equal(
    (await fetchSlot(slots[1], "/", slots[0].accessToken)).status,
    403,
  );
  assert.equal((await fetchSlot(slots[0])).status, 503);
  const alice = await agent.newSession({ workspaceId: a.workspaceId });
  await write(
    alice,
    "index.html",
    '<h1>Genie garden</h1><script type="module" src="/site.js"></script>',
  );
  await write(
    alice,
    "site.js",
    'document.body.dataset.revision="one"; if (import.meta.hot) import.meta.hot.accept();',
  );
  const proc = await start(alice, vite);
  await page(slots[0], "Genie garden");
  await start(
    alice,
    "exec node -e \"require('node:http').createServer((q,r)=>r.end('second port')).listen(5101,'0.0.0.0')\"",
  );
  await page(slots[1], "second port");
  assert.equal((await fetchSlot(slots[0], "/site.js")).status, 200);
  await barrier("loaded", { slots: slots.slice(0, 2) });
  await call(alice, "edit_file", {
    path: "site.js",
    old_text: '"one"',
    new_text: '"two"',
  });
  await barrier("changed", {});
  assert.equal(
    (await call(alice, "process_status", { process_id: proc.process_id }))
      .status,
    "running",
  );
  await call(alice, "process_logs", { process_id: proc.process_id });
  const b = await sites.getOrCreate({
    key: key + "-bob",
    options: { previewPortCount: 15 },
  });
  assert.equal(b.previews.length, 15);
  assert.equal(
    (await fetchSlot(b.previews[0], "/", slots[0].accessToken)).status,
    403,
  );
  const bob = await agent.newSession({ workspaceId: b.workspaceId });
  await write(bob, "index.html", "<h1>Bob studio</h1>");
  await start(bob, vite);
  await page(b.previews[0], "Bob studio");
  await page(slots[0], "Genie garden");
  await close(bob);
  await sites.stopWorkspace(b.workspaceId);
  await sites.deleteData(b.dataId);
  await call(alice, "process_stop", { process_id: proc.process_id });
  await close(alice);
  await barrier("replace", { workspaceId: a.workspaceId });
  const replacement = await sites.getOrCreate({ key });
  assert.equal(replacement.workspaceId, a.workspaceId);
  assert.equal(replacement.dataId, a.dataId);
  for (const old of slots) assert.equal((await fetchSlot(old)).status, 410);
  assert.notEqual(host(replacement.previews[0]), host(slots[0]));
  await sites.stopWorkspace(a.workspaceId);
  assert.equal((await fetchSlot(replacement.previews[0])).status, 410);
  const tomorrow = await sites.getOrCreate({ key });
  assert.equal(tomorrow.dataId, a.dataId);
  assert.notEqual(tomorrow.workspaceId, a.workspaceId);
  const resumed = await agent.newSession({ workspaceId: tomorrow.workspaceId });
  await start(resumed, vite);
  await page(tomorrow.previews[0], "Genie garden");
  await close(resumed);
  await sites.stopWorkspace(tomorrow.workspaceId);
  await sites.deleteData(tomorrow.dataId);
  console.log(
    "PASS: workspace ToolRunner, preview slots, live Vite, project isolation, replacement and reopen",
  );
} finally {
  svc.disconnect();
}
