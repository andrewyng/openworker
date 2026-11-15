// Real managed Rust host and request-sensitive loopback SSE. No cloud keys.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 60000 };
for (const mode of ["default", "opt-out", "unreported"])
  test(`Chat Completions streamed usage: ${mode}`, options, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-chat-usage-"));
    const requests = [];
    const server = createServer(async (req, res) => {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const input = JSON.parse(raw);
      requests.push(input);
      // Emulate a server that cannot accept this optional capability.
      if (mode === "opt-out" && input.stream_options !== undefined) {
        res.writeHead(400);
        res.end('{"error":{"message":"stream_options unsupported"}}');
        return;
      }
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const write = (value) => res.write(`data: ${JSON.stringify(value)}\n\n`);
      write({ id: "fixture", choices: [{ index: 0, delta: { role: "assistant", content: "Hello " }, finish_reason: null }] });
      write({ id: "fixture", choices: [{ index: 0, delta: { content: "there." }, finish_reason: "stop" }] });
      if (input.stream_options?.include_usage === true && mode !== "unreported") {
        write({ id: "fixture", choices: [], usage: {
          prompt_tokens: 42, completion_tokens: 7, total_tokens: 49,
          prompt_tokens_details: { cached_tokens: 12 },
        } });
      }
      res.end("data: [DONE]\n\n");
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let harness;
    t.after(async () => {
      await harness?.shutdown();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await rm(root, { recursive: true, force: true });
    });
    harness = await Harness.start({ executable: process.env.OW_TS_TEST_HOST, stateDir: root, sandbox: "off" });
    await harness.setModelProvider({
      name: "UsageFixture", api: "chat-completions",
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
      ...(mode === "opt-out" ? { streamUsage: false } : {}),
    });
    const session = await harness.createAgent({
      name: "usage-check", instructions: "Reply briefly.", tools: [],
      planning: false, memory: false, scheduling: false,
      defaultModel: { provider: "UsageFixture", name: "fixture" },
    }).newSession();
    const run = await session.send("Hello.");
    const usage = [];
    const deltas = [];
    for await (const item of run.events()) {
      if (item.event.type === "assistant_delta") deltas.push(item.event.data.text);
      if (item.event.type === "model_usage") usage.push(item.event.data);
    }
    const result = await run.result();
    assert.equal(result.status, "completed");
    assert.equal(result.finalOutput, "Hello there.");
    assert.equal(deltas.join(""), "Hello there.");
    assert.equal(requests.length, 1, "No fallback/repeated model request");
    assert.equal(requests[0].stream, true);
    if (mode === "opt-out") assert.equal(requests[0].stream_options, undefined);
    else assert.deepEqual(requests[0].stream_options, { include_usage: true });
    if (mode === "default") {
      assert.equal(usage.length, 1);
      assert.equal(usage[0].input_tokens, 42);
      assert.equal(usage[0].output_tokens, 7);
      assert.equal(usage[0].cached_input_tokens, 12);
    } else {
      assert.deepEqual(usage, [], "Missing usage must not be invented as zero");
    }
  });
