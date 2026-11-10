// Gemini-compatible synthetic SSE + a real SDK host. No Google credentials.
// The fixture verifies both required signature replay and observed indexless
// complete-call framing. It is not the partner's captured failing response.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 90000 };
const signatureA = "OPAQUE_GOOGLE_SIGNATURE_A";
const signatureB = "OPAQUE_GOOGLE_SIGNATURE_B";
const signatureText = "OPAQUE_GOOGLE_TEXT_SIGNATURE";
const extra = (signature) => ({ google: { thought_signature: signature } });

for (const mode of ["normal", "partial", "conflicting", "missing-done"]) {
  test(`Gemini compatible signed tool loop: ${mode}`, options, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-gemini-compat-"));
    const requests = [];
    const failures = [];
    const server = createServer(async (req, res) => {
      try {
        let raw = "";
        for await (const chunk of req) raw += chunk;
        const input = JSON.parse(raw);
        requests.push(input);
        assert.equal(input.stream, true);
        assert.deepEqual(input.stream_options, { include_usage: true });
        const n = requests.length;
        if (mode === "normal" && n > 1) {
          const calls = input.messages.flatMap((m) => m.tool_calls ?? []);
          assert.equal(calls[0].extra_content.google.thought_signature, signatureA);
          assert.deepEqual(JSON.parse(calls[0].function.arguments), { name: "first" });
          assert.ok(input.messages.some((m) => m.role === "tool" && m.tool_call_id === "google-call-a"));
          if (n > 2) {
            assert.equal(calls[1].extra_content.google.thought_signature, signatureB);
            assert.deepEqual(JSON.parse(calls[1].function.arguments), { name: "second" });
            assert.ok(input.messages.some((m) => m.role === "tool" && m.tool_call_id === "google-call-b"));
          }
          if (n > 3) {
            assert.ok(input.messages.some((m) => m.role === "assistant" && m.extra_content?.google?.thought_signature === signatureText));
          }
        }
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        const chunk = (delta, finish_reason = null) => ({ id: `response-${n}`, choices: [{ index: 0, delta, finish_reason }] });
        const write = (event) => {
          const frame = Buffer.from(`data: ${JSON.stringify(event)}\n\n`);
          // Exercise SSE and UTF-8 parser boundaries independently of objects.
          res.write(frame.subarray(0, 13)); res.write(frame.subarray(13));
        };
        if (mode !== "normal" || n === 1) {
          const call = { id: "google-call-a", type: "function", function: { name: "app__lookup", arguments: mode === "partial" ? "{" : '{"name":"first"}' }, extra_content: extra(signatureA) };
          if (mode === "conflicting") call.index = 0;
          write(chunk({ role: "assistant", tool_calls: [call] }));
          if (mode === "conflicting") write(chunk({ tool_calls: [{ index: 0, extra_content: extra("CHANGED_SIGNATURE") }] }));
          write(chunk({ role: "assistant" }, "stop"));
        } else if (n === 2) {
          write(chunk({ role: "assistant", tool_calls: [{ index: 0, id: "google-call-b", type: "function", function: { name: "app__lookup", arguments: '{"name":' }, extra_content: extra(signatureB) }] }));
          write(chunk({ tool_calls: [{ index: 0, function: { arguments: '"second"}' } }] }, "tool_calls"));
        } else {
          write(chunk({ role: "assistant", content: "Both checked." }));
          write(chunk({ role: "assistant", content: "", extra_content: extra(signatureText) }, "stop"));
        }
        if (mode !== "missing-done") {
          write({ id: `response-${n}`, choices: [], usage: { prompt_tokens: 42, completion_tokens: 7, total_tokens: 49 } });
          res.end("data: [DONE]\n\n");
        } else res.end();
      } catch (error) {
        failures.push(error);
        if (!res.headersSent) res.writeHead(400);
        res.end('{"error":{"message":"fixture request validation failed"}}');
      }
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let harness;
    t.after(async () => {
      await harness?.shutdown(); server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await rm(root, { recursive: true, force: true });
    });
    const startOptions = { executable: process.env.OW_TS_TEST_HOST, stateDir: root, sandbox: "off" };
    const provider = { name: "GeminiFixture", api: "chat-completions", baseUrl: `http://127.0.0.1:${server.address().port}/v1/openai` };
    const definition = { name: "gemini-check", instructions: "Check both names, then explain.", tools: ["app.lookup"], approvals: { mode: "only", tools: [] }, planning: false, memory: false, scheduling: false, defaultModel: { provider: "GeminiFixture", name: "gemini-fixture" } };
    let effects = 0;
    const tools = { namespace: "app", tools: { lookup: {
      description: "Read a synthetic name status.", inputSchema: { type: "object", properties: { name: { type: "string" } }, required: ["name"], additionalProperties: false },
      execute: (args) => { effects++; assert.ok(["first", "second"].includes(args.name)); return { available: true }; },
    } } };
    async function start() {
      harness = await Harness.start(startOptions);
      await harness.setModelProvider(provider);
      await harness.registerTools(tools);
      return harness.createAgent(definition);
    }
    const agent = await start();
    const session = await agent.newSession();
    const run = await session.send("Check first and second.");
    const events = [];
    for await (const item of run.events()) events.push(item);
    const result = await run.result();
    assert.deepEqual(failures, []);
    if (mode !== "normal") {
      assert.equal(result.status, "failed");
      assert.equal(effects, 0, "Invalid or incomplete signed output cannot execute tools");
      return;
    }
    assert.equal(result.status, "completed");
    assert.equal(result.finalOutput, "Both checked.");
    assert.equal(effects, 2);
    assert.equal(requests.length, 3);
    for (const signature of [signatureA, signatureB, signatureText]) {
      assert.ok(!JSON.stringify(events).includes(signature), "Private signatures must not appear in public events");
      assert.ok(!JSON.stringify(await session.snapshot()).includes(signature), "Private signatures must not appear in display snapshots");
    }
    const reference = session.reference();
    await harness.shutdown();
    const restartedAgent = await start();
    const restored = await restartedAgent.resumeSession(reference);
    assert.equal((await (await restored.send("Explain again briefly.")).result()).status, "completed");
    assert.deepEqual(failures, []);
    assert.equal(effects, 2);
    assert.equal(requests.length, 4, "Stored signatures must survive host restart");
  });
}
