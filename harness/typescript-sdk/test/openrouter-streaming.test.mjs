// Real Rust host + private stdio + HTTP/SSE. Published OpenRouter frame shape;
// synthetic loopback responses, no cloud requests or provider credentials.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 60000 };
async function setup(t, mode) {
  const root = await mkdtemp(join(tmpdir(), "ow-openrouter-stream-"));
  const state = { effects: 0, requests: 0, finished: false };
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const timer = setTimeout(release, 15000);
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) {
      raw += chunk;
      if (raw.length > 1000000) {
        res.writeHead(413);
        res.end();
        return;
      }
    }
    const input = JSON.parse(raw);
    state.requests++;
    assert.equal(req.headers.authorization, undefined);
    assert.equal(input.stream, true);
    const tool =
      mode !== "text" && !input.messages.some((m) => m.role === "tool");
    const reason = tool ? "tool_calls" : "stop";
    const id = `gen-fixture-${state.requests}`;
    const chunk = (delta, finish_reason = null) => ({
      id,
      choices: [
        { index: 0, delta, finish_reason, native_finish_reason: finish_reason },
      ],
    });
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.flushHeaders();
    const write = (value) => {
      const frame = Buffer.from("data: " + JSON.stringify(value) + "\r\n\r\n");
      // Frame boundaries need not match HTTP write/read boundaries.
      res.write(frame.subarray(0, 9));
      res.write(frame.subarray(9));
    };
    res.write(": OPENROUTER PROCESSING\r\n\r\n");
    if (tool) {
      write(
        chunk({
          tool_calls: [
            {
              index: 0,
              id: "call-preview",
              type: "function",
              function: { name: "app__preview", arguments: '{"label":' },
            },
          ],
        }),
      );
      write(
        chunk({ tool_calls: [{ index: 0, function: { arguments: '"ok"}' } }] }),
      );
    } else {
      write(chunk({ role: "assistant", content: "Hello " }));
      if (mode === "text") await gate;
      write(chunk({ content: "streaming world." }));
    }
    write(chunk({ role: "assistant", content: "" }, reason));
    const tail = chunk({ role: "assistant", content: "" }, reason);
    tail.usage = { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 };
    if (mode === "late-content")
      tail.choices[0].delta.content = "unexpected late text";
    if (mode === "conflicting-reason") tail.choices[0].finish_reason = "stop";
    if (mode === "choice-error")
      tail.choices[0].error = { code: "server_error" };
    write(tail);
    state.finished = true;
    res.end("data: [DONE]\r\n\r\n");
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  let h;
  t.after(async () => {
    release();
    clearTimeout(timer);
    await h?.shutdown();
    server.closeAllConnections();
    await new Promise((r) => server.close(r));
    await rm(root, { recursive: true, force: true });
  });
  h = await Harness.start({
    executable: process.env.OW_TS_TEST_HOST,
    stateDir: root,
    sandbox: "off",
  });
  await h.setModelProvider({
    name: "RouterFixture",
    api: "chat-completions",
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
  });
  await h.registerTools({
    namespace: "app",
    tools: {
      preview: {
        description: "Synthetic app preview.",
        inputSchema: {
          type: "object",
          properties: { label: { type: "string" } },
          required: ["label"],
          additionalProperties: false,
        },
        execute: async (args) => {
          assert.deepEqual(args, { label: "ok" });
          state.effects++;
          return { shown: true };
        },
      },
    },
  });
  const session = await h
    .createAgent({
      name: "streaming-check",
      instructions: "Use the supplied tool when needed.",
      tools: ["app.preview"],
      defaultModel: { provider: "RouterFixture", name: "fixture" },
    })
    .newSession();
  return { session, state, release };
}
test(
  "OpenRouter text deltas arrive before completion; usage tail does not duplicate text",
  options,
  async (t) => {
    const { session, state, release } = await setup(t, "text");
    const run = await session.send("Stream a response.");
    const deltas = [];
    for await (const event of run.events())
      if (event.event.type === "assistant_delta") {
        if (!deltas.length) {
          assert.equal(state.finished, false);
          release();
        }
        deltas.push(event.event.data.text);
      }
    const result = await run.result();
    assert.equal(result.status, "completed");
    // The ordinary terminal chunk retains its existing empty delta; the extra
    // accounting chunk must not yield another delta or duplicate any text.
    assert.deepEqual(deltas, ["Hello ", "streaming world.", ""]);
    assert.equal(result.finalOutput, deltas.join(""));
    assert.equal(state.requests, 1);
    assert.equal(state.effects, 0);
  },
);
test(
  "OpenRouter tool usage tail preserves approval and exactly-once callback execution",
  options,
  async (t) => {
    const { session, state } = await setup(t, "tool");
    const run = await session.send("Show the preview.");
    const pending = await run.result();
    assert.equal(pending.status, "interrupted");
    assert.equal(pending.interruptions.length, 1);
    assert.equal(state.effects, 0);
    await session.send({
      type: "approval",
      interactionId: pending.interruptions[0].id,
      decision: "once",
    });
    assert.equal((await run.result()).status, "completed");
    assert.equal(state.effects, 1);
    assert.equal(state.requests, 2);
    for await (const _ of run.events()) {
    }
    assert.equal((await run.result()).status, "completed");
    assert.equal(state.effects, 1);
    assert.equal(state.requests, 2);
  },
);
for (const mode of ["late-content", "conflicting-reason", "choice-error"])
  test(
    `Unsafe OpenRouter accounting tail rejected before tool dispatch: ${mode}`,
    options,
    async (t) => {
      const { session, state } = await setup(t, mode);
      const result = await (await session.send("Show the preview.")).result();
      assert.equal(result.status, "failed");
      assert.equal(result.interruptions.length, 0);
      assert.equal(state.effects, 0);
      assert.equal(state.requests, 1);
    },
  );
