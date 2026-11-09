// Request-sensitive synthetic cache accounting, not a claim of a live cache hit.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 60000 };
for (const enabled of [true, false]) {
  test(`Anthropic automatic caching ${enabled ? "default" : "opt-out"}`, options, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-anthropic-caching-"));
    const requests = [];
    const server = createServer(async (req, res) => {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const input = JSON.parse(raw);
      requests.push(input);
      if (!enabled && input.cache_control !== undefined) {
        res.writeHead(400); res.end('{"error":{"type":"invalid_request_error","message":"cache_control unsupported"}}'); return;
      }
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const write = (event) => res.write(`data: ${JSON.stringify(event)}\n\n`);
      const tool = requests.length === 1;
      const cache = input.cache_control?.type === "ephemeral";
      write({ type: "message_start", message: { role: "assistant", content: [], usage: {
        input_tokens: cache ? 10 : 90,
        cache_read_input_tokens: cache && !tool ? 80 : 0,
        cache_creation_input_tokens: cache && tool ? 80 : 0,
        output_tokens: 0,
      } } });
      write({ type: "content_block_start", index: 0, content_block: tool
        ? { type: "tool_use", id: "cache-call", name: "app__check", input: {} }
        : { type: "text", text: "" } });
      write({ type: "content_block_delta", index: 0, delta: tool
        ? { type: "input_json_delta", partial_json: "{}" }
        : { type: "text_delta", text: "Checked." } });
      write({ type: "content_block_stop", index: 0 });
      write({ type: "message_delta", delta: { stop_reason: tool ? "tool_use" : "end_turn" }, usage: { output_tokens: 5 } });
      write({ type: "message_stop" });
      res.end();
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let harness;
    t.after(async () => {
      await harness?.shutdown(); server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await rm(root, { recursive: true, force: true });
    });
    harness = await Harness.start({ executable: process.env.OW_TS_TEST_HOST, stateDir: root, sandbox: "off" });
    const provider = { name: "ClaudeFixture", api: "anthropic", baseUrl: `http://127.0.0.1:${server.address().port}/v1`, credentials: { apiKey: "fixture-key-only", storage: "memory" } };
    await harness.setModelProvider({ ...provider, ...(enabled ? {} : { promptCaching: false }) });
    // A key rotation without options must not silently re-enable an opt-out.
    await harness.setModelProvider({ ...provider, credentials: { apiKey: "fixture-key-rotated", storage: "memory" } });
    let effects = 0;
    await harness.registerTools({ namespace: "app", tools: { check: {
      description: "Read a synthetic value.", inputSchema: { type: "object", properties: {}, additionalProperties: false },
      execute: () => { effects++; return { checked: true }; },
    } } });
    const session = await harness.createAgent({ name: "cache-check", instructions: "Check then explain.", tools: ["app.check"], approvals: { mode: "only", tools: [] }, planning: false, memory: false, scheduling: false, defaultModel: { provider: "ClaudeFixture", name: "fixture" } }).newSession();
    const run = await session.send("Check the value.");
    const usage = [];
    for await (const item of run.events()) if (item.event.type === "model_usage") usage.push(item.event.data);
    assert.equal((await run.result()).status, "completed");
    assert.equal(effects, 1);
    assert.equal(requests.length, 2);
    for (const request of requests) {
      if (enabled) assert.deepEqual(request.cache_control, { type: "ephemeral" });
      else assert.equal(request.cache_control, undefined);
    }
    assert.equal(requests[0].system, requests[1].system);
    assert.deepEqual(requests[0].tools, requests[1].tools);
    assert.ok(requests[1].messages.some((m) => m.content.some((c) => c.type === "tool_result")));
    assert.equal(usage.length, 2);
    assert.equal(usage[0].cache_write_input_tokens, enabled ? 80 : 0);
    assert.equal(usage[1].cache_read_input_tokens, enabled ? 80 : 0);
    assert.equal(usage[0].input_tokens, 90);
    assert.equal(usage[1].input_tokens, 90);
  });
}
