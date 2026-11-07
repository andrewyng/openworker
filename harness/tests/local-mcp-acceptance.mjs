// Real native harness + independent loopback MCP service; no model/auth/network account.
import { createServer } from "node:http";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { homedir } from "node:os";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import { RuntimeClient } from "./support/runtime.mjs";
delete process.env.OPENAI_API_KEY;
const binary = resolve(process.env.OPENWORKER_BIN || "target/debug/openworker");
const root = resolve(
  process.env.OPENWORKER_TEST_DATA ||
    join(homedir(), "openworker-http-acceptance"),
  randomUUID(),
);
await mkdir(root, { recursive: true, mode: 0o700 });
execFileSync(binary, ["prepare-directory", "--path", root]);
let calls = 0,
  redirectHits = 0,
  sessionCounter = 0,
  deleted = 0,
  cancellations = 0;
const sessions = new Set();
const server = createServer(async (req, res) => {
  if (req.url === "/redirected") {
    redirectHits++;
    res.end("{}");
    return;
  }
  if (req.method === "GET" && req.url === "/health") {
    res.end("alive");
    return;
  }
  if (req.method === "DELETE") {
    sessions.delete(req.headers["mcp-session-id"]);
    deleted++;
    res.writeHead(204).end();
    return;
  }
  let text = "";
  for await (const chunk of req) text += chunk;
  const m = JSON.parse(text);
  const result = (r) =>
    res
      .writeHead(200, { "Content-Type": "application/json" })
      .end(JSON.stringify({ jsonrpc: "2.0", id: m.id, result: r }));
  if (m.method === "initialize") {
    const id = "test-" + ++sessionCounter;
    sessions.add(id);
    res.setHeader("Mcp-Session-Id", id);
    result({
      protocolVersion: "2025-11-25",
      capabilities: { tools: {} },
      serverInfo: { name: "external-local-fixture", version: "1" },
    });
    return;
  }
  if (!sessions.has(req.headers["mcp-session-id"])) {
    res.writeHead(404).end();
    return;
  }
  if (m.method === "notifications/initialized") {
    res.writeHead(202).end();
    return;
  }
  if (m.method === "notifications/cancelled") {
    cancellations++;
    res.writeHead(202).end();
    return;
  }
  if (m.method === "tools/list") {
    result({
      tools: ["status", "hidden"].map((name) => ({
        name,
        description: "Synthetic local device status",
        inputSchema: {
          type: "object",
          properties: {
            mode: {
              type: "string",
              enum: ["normal", "disconnect", "redirect", "delay"],
            },
          },
          required: ["mode"],
          additionalProperties: false,
        },
      })),
    });
    return;
  }
  if (m.method === "tools/call") {
    calls++;
    if (m.params.arguments.mode === "disconnect") {
      res.destroy();
      return;
    }
    if (m.params.arguments.mode === "redirect") {
      res
        .writeHead(307, {
          Location: `http://127.0.0.1:${server.address().port}/redirected`,
        })
        .end();
      return;
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(
      "event: message\r\ndata: " +
        JSON.stringify({
          jsonrpc: "2.0",
          method: "notifications/progress",
          params: { progress: 1, total: 2 },
        }) +
        "\r\n\r\n",
    );
    const data =
      "event: message\r\ndata: " +
      JSON.stringify({
        jsonrpc: "2.0",
        id: m.id,
        result: {
          content: [{ type: "text", text: "Synthetic battery: 73% ✓" }],
          isError: false,
        },
      }) +
      "\r\n\r\n";
    if (m.params.arguments.mode === "delay") {
      res.on("close", () => {});
      return;
    }
    res.write(data.slice(0, 17));
    setTimeout(() => res.end(data.slice(17)), 20);
    return;
  }
  res.writeHead(400).end();
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/mcp`;
const bundle = join(root, "bundle"),
  work = join(root, "workspace"),
  state = join(root, "state");
await mkdir(join(bundle, "coworker"), { recursive: true });
await mkdir(work);
await writeFile(
  join(bundle, "coworker/manifest.md"),
  "---\nid: local-test\nname: Local service acceptance\n---\nUse only selected tools.",
);
await writeFile(
  join(bundle, "tools.json"),
  JSON.stringify({ version: 3, tools: ["acer.status"] }),
);
const config = join(root, "servers.json");
await writeFile(
  config,
  JSON.stringify({
    version: 1,
    servers: [{ namespace: "acer", transport: "streamable_http", url }],
  }),
);
let client;
function start() {
  client = new RuntimeClient(binary, [
    "serve",
    "--stdio",
    "--sandbox",
    "off",
    "--scripted",
    "--state-dir",
    state,
    "--bundle",
    bundle,
    "--servers",
    config,
  ]);
}
async function init() {
  await client.request("initialize", { protocol_version: "0.1" });
}
async function until(fn, p, label) {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    const v = await fn();
    if (p(v)) return v;
    await new Promise((r) => setTimeout(r, 30));
  }
  throw Error("Timed out " + label);
}
async function session(
  mode = "normal",
  permission = "ask",
  name = "acer__status",
  argumentsOverride,
) {
  const { session } = await client.request("session/create", {
    name: "Local MCP fixture",
    coworker: "local-test",
    workspace: work,
    permissions: { mode: permission },
  });
  await client.request("turn/start", {
    session_id: session.id,
    submission_id: randomUUID(),
    text:
      "TEST_RESOURCE_GRANTS " +
      JSON.stringify([{ name, arguments: argumentsOverride || { mode } }]),
  });
  return session.id;
}
async function snapshot(sid) {
  return client.request("session/attach", { session_id: sid });
}
async function terminal(sid) {
  return until(
    () => snapshot(sid),
    (s) =>
      ["completed", "failed", "cancelled", "interrupted"].includes(
        s.turn?.status,
      ),
    "terminal",
  );
}
async function respond(sid, iid, decision) {
  await until(
    async () => {
      try {
        await client.request("interaction/respond", {
          session_id: sid,
          interaction_id: iid,
          ...(typeof decision === "string" ? { decision } : decision),
        });
        return true;
      } catch (e) {
        if (!e.message.includes("checkpointing")) throw e;
        return false;
      }
    },
    Boolean,
    "response",
  );
}
try {
  start();
  await init();
  const sid = await session();
  let s = await until(
    () => snapshot(sid),
    (s) => s.interactions.length,
    "approval",
  );
  const iid = s.interactions[0].id;
  assert.equal(calls, 0);
  await client.stop();
  start();
  await init();
  s = await snapshot(sid);
  assert.equal(s.interactions[0].id, iid);
  await respond(sid, iid, "once");
  assert.equal((await terminal(sid)).turn.status, "completed");
  assert.equal(calls, 1);
  await client.stop();
  start();
  await init();
  assert.equal((await snapshot(sid)).turn.status, "completed");
  assert.equal(calls, 1);
  const denied = await session();
  s = await until(
    () => snapshot(denied),
    (s) => s.interactions.length,
    "deny",
  );
  await respond(denied, s.interactions[0].id, "deny");
  await terminal(denied);
  assert.equal(calls, 1);
  const question = await session("normal", "bypass", "ask_user", {
    question: "Review focus?",
    options: ["Correctness", "Security"],
  });
  const q = await until(
    () => snapshot(question),
    (s) => s.interactions.length,
    "question",
  );
  assert.equal(q.interactions[0].kind, "question");
  await client.stop();
  start();
  await init();
  assert.equal(
    (await snapshot(question)).interactions[0].id,
    q.interactions[0].id,
  );
  await assert.rejects(
    client.request("interaction/respond", {
      session_id: question,
      interaction_id: q.interactions[0].id,
      answer: "Unknown",
    }),
  );
  await respond(question, q.interactions[0].id, { answer: "Correctness" });
  assert.equal((await terminal(question)).turn.status, "completed");
  const answers = await client.request("event/read", {
    session_id: question,
    after: 0,
  });
  assert.equal(
    answers.events.find(
      (e) => e.type === "tool_finished" && e.data.name === "ask_user",
    ).data.result.answer,
    "Correctness",
  );
  const hidden = await session("normal", "bypass", "acer__hidden");
  assert.equal((await terminal(hidden)).turn.status, "failed");
  assert.equal(calls, 1);
  for (const mode of ["redirect", "disconnect"]) {
    const before = calls;
    const id = await session(mode, "bypass");
    assert.equal((await terminal(id)).turn.status, "failed");
    assert.equal(calls, before + 1);
  }
  assert.equal(redirectHits, 0);
  const beforeSlow = calls;
  const slow = await session("delay", "bypass");
  await until(
    () => calls,
    (n) => n === beforeSlow + 1,
    "actual in-flight HTTP call",
  );
  const beforeCancel = cancellations;
  s = await until(
    () => snapshot(slow),
    (s) => s.turn?.status === "executing",
    "executing",
  );
  await client.request("turn/cancel", { session_id: slow, turn_id: s.turn.id });
  assert.equal((await terminal(slow)).turn.status, "cancelled");
  await client.stop();
  assert.ok(cancellations > beforeCancel, "MCP advisory cancellation was sent");
  assert.equal(
    await (await fetch(url.replace("/mcp", "/health"))).text(),
    "alive",
  );
  assert.ok(deleted > 0);
  const report = {
    passed: true,
    platform: process.platform,
    root,
    checks: [
      "servers.json qualified selection",
      "independent loopback HTTP MCP + split SSE response",
      "approval identity and results survive runtime restart",
      "Q&A stays separate from bypass, survives restart, and validates the human answer",
      "deny and unselected tools never invoke server",
      "redirect blocked; uncertain disconnect never automatically replayed",
      "cancel pending HTTP request",
      "runtime shutdown disconnects its session and leaves external service alive",
    ],
    calls,
    deleted,
    cancellations,
  };
  await writeFile(join(root, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally {
  await client?.stop();
  server.closeAllConnections();
  await new Promise((r) => server.close(r));
}
