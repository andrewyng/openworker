import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { setTimeout as wait } from "node:timers/promises";
import { Harness } from "../dist/index.js";
import { attachIntelIma } from "../dist/adapters/intel-ima.js";
import { mockIma } from "./fixtures/mock-ima.mjs";

const live = { skip: !process.env.OW_TS_TEST_HOST, timeout: 35000 };
async function until(fn) {
  for (let n = 0; n < 160; n++) {
    if (await fn()) return;
    await wait(50);
  }
  assert.fail("Timed out waiting for test observation");
}
async function setup(t, scenario, api = "chat-completions", notify = true) {
  let endpoint = process.env.OW_MOCK_IMA_URL;
  if (!endpoint) {
    const server = mockIma();
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    endpoint = "http://127.0.0.1:" + server.address().port;
    t.after(() => {
      server.closeAllConnections();
      server.close();
    });
  }
  const base = endpoint + "/" + scenario + "-" + randomUUID() + "/v1";
  const root = await mkdtemp(join(tmpdir(), "ow-provider-events-"));
  const harness = await Harness.start({
    executable: process.env.OW_TS_TEST_HOST,
    stateDir: root,
    sandbox: "off",
  });
  const events = [],
    delivered = new Set();
  let adapter,
    effects = 0;
  t.after(async () => {
    await harness.shutdown();
    await adapter?.close();
    await rm(root, { recursive: true, force: true });
  });
  if (scenario === "generic") {
    await harness.setModelProvider({
      name: "Fixture",
      api,
      baseUrl: base,
      credentials: { apiKey: "fixture-key", storage: "memory" },
      inferenceOptions: {
        headers: { "x-example": "blue" },
        query: { "api-version": "a&b" },
        body: { sampling_hint: "local" },
      },
    });
  } else {
    adapter = await attachIntelIma(harness, {
      provider: "Fixture",
      api,
      baseUrl: base,
      apiKey: "fixture-key",
      route: { mode: "pin", target: "fixture" },
      onState: (e) => {
        const id = e.sessionId + ":" + e.sequence;
        if (!delivered.has(id)) {
          delivered.add(id);
          events.push(e);
        }
      },
    });
  }
  await harness.registerTools({
    namespace: "app",
    tools: {
      probe: {
        description: "Run the probe.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        execute: () => {
          effects++;
          return { ok: true };
        },
      },
    },
  });
  const agent = harness.createAgent({
    name: "tester",
    instructions: "Use the requested tool.",
    tools: ["app.probe"],
    approvals: { mode: "always" },
    autoSkipQuestions: false,
    planning: false,
    memory: false,
    scheduling: false,
    defaultModel: {
      name: "fixture",
      provider: "Fixture",
      notifyOnSessionState: notify,
    },
  });
  const s = await agent.newSession({ autoSkipQuestions: false });
  return {
    s,
    events,
    effects: () => effects,
    rows: async () => await (await fetch(base + "/observations")).json(),
  };
}
for (const [scenario, waiting, done] of [
  ["approve", "waiting_user_approval", "user_approval_done"],
  ["deny", "waiting_user_approval", "user_approval_done"],
  ["question", "waiting_user_response", "user_response_done"],
  ["outage", "waiting_user_approval", "user_approval_done"],
]) {
  test("provider observer " + scenario, live, async (t) => {
    const { s, events, rows, effects } = await setup(t, scenario);
    const result = await s.run("Use a tool");
    assert.equal(result.status, "interrupted");
    await until(() => events.some((e) => e.state === waiting));
    const interruption = result.interruptions[0];
    const decision = scenario === "deny" ? "deny" : "once";
    await s.respond(
      interruption,
      scenario === "question" ? { answer: "A" } : { decision },
    );
    await until(
      async () => (await rows()).filter((r) => r.state === "start").length >= 2,
    );
    await until(() => events.some((e) => e.state === "turn_completed"));
    await s.close();
    await until(async () => (await rows()).some((r) => r.state === "done"));
    const pair = events.filter((e) => [waiting, done].includes(e.state));
    assert.deepEqual(
      pair.map((e) => e.state),
      [waiting, done],
    );
    assert.equal(pair[1].interactionId, interruption.id);
    assert.equal(pair[0].interactionId, interruption.id);
    assert.ok(pair[0].sequence < pair[1].sequence);
    if (scenario === "deny")
      assert.ok(!events.some((e) => e.state === "waiting_tool_completion"));
    if (scenario !== "question")
      assert.equal(
        pair[1].decision,
        decision === "deny" ? "denied" : "approved",
      );
    assert.equal(effects(), ["approve", "outage"].includes(scenario) ? 1 : 0);
    assert.ok((await rows()).some((r) => r.state === "waiting_confirm"));
  });
}
for (const [scenario, api, notify] of [
  ["plain", "chat-completions", false],
  ["anthropic", "anthropic", true],
  ["missing", "chat-completions", true],
  ["generic", "chat-completions", false],
]) {
  test(scenario + " request", live, async (t) => {
    const { s, rows } = await setup(t, scenario, api, notify);
    const result = await s.run("Hello");
    assert.equal(
      result.status,
      scenario === "missing" ? "failed" : "completed",
    );
    if (scenario === "plain") {
      await s.run("Second turn");
      await s.close();
      assert.ok((await rows()).every((r) => !r.state));
    }
    if (scenario === "generic") {
      const [r] = await rows();
      assert.deepEqual([r.header, r.query, r.option], ["blue", "a&b", "local"]);
    }
  });
}
test("cancel active inference", live, async (t) => {
  const { s, rows } = await setup(t, "cancel");
  const run = await s.send("Wait");
  await until(async () => (await rows()).some((r) => r.state === "start"));
  await s.cancel();
  await until(async () => (await rows()).some((r) => r.cancel));
  assert.equal((await run.result()).status, "cancelled");
});

test(
  "a contradictory late route receipt fails without replaying the request",
  live,
  async (t) => {
    const { s, rows, effects } = await setup(t, "conflict");
    assert.equal((await s.run("Use a tool")).status, "failed");
    assert.equal(
      (await rows()).filter((row) => row.state === "start").length,
      1,
    );
    assert.equal(effects(), 0);
  },
);
