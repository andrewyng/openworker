// Opt-in paid smoke test: streamed tool use, accounting, and durable replay.
// Only synthetic data and a local no-side-effect lookup are used.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

if (process.env.OW_RUN_LIVE !== "1") throw new Error("Set OW_RUN_LIVE=1 for paid qualification");
for (const key of ["OW_TS_TEST_HOST", "OW_LIVE_MODEL", "OW_LIVE_API", "OW_LIVE_KEY_ENV"])
  if (!process.env[key]) throw new Error(`${key} is required`);
assert.ok(["responses", "chat-completions", "anthropic"].includes(process.env.OW_LIVE_API));
assert.ok(process.env[process.env.OW_LIVE_KEY_ENV], "Provider key is missing");
if (process.env.OW_LIVE_API === "chat-completions")
  assert.ok(process.env.OW_LIVE_BASE_URL, "Compatible providers require an explicit endpoint");

const stateDir = await mkdtemp(join(tmpdir(), "ow-live-tools-"));
const receipt = `check-${randomUUID()}`;
let harness;
let toolCalls = 0;
async function start() {
  harness = await Harness.start({ executable: process.env.OW_TS_TEST_HOST, stateDir, sandbox: "off" });
  await harness.setModelProvider({
    name: "LiveQualification", api: process.env.OW_LIVE_API,
    ...(process.env.OW_LIVE_BASE_URL ? { baseUrl: process.env.OW_LIVE_BASE_URL } : {}),
    credentials: { apiKeyEnv: process.env.OW_LIVE_KEY_ENV, storage: "memory" },
    ...(process.env.OW_LIVE_API === "anthropic" ? { thinking: { type: "adaptive" } } : {}),
  });
  await harness.registerTools({ namespace: "app", tools: { lookup: {
    description: "Read a synthetic price and receipt for atlas.example. This performs no external action.",
    inputSchema: { type: "object", properties: { name: { type: "string", enum: ["atlas.example"] } }, required: ["name"], additionalProperties: false },
    execute(args) {
      assert.equal(args.name, "atlas.example");
      toolCalls++;
      assert.ok(toolCalls <= 1, "The previously completed lookup must not replay");
      return { name: args.name, priceUsd: 12.34, receipt };
    },
  } } });
  return harness.createAgent({
    name: "tool-qualification", instructions: "Follow the test request precisely. Use app.lookup only when requested. Copy the tool receipt exactly into your final answer. Keep final answers under 60 words.",
    tools: ["app.lookup"], builtinTools: { askUser: false, readHistory: false },
    planning: false, memory: false, scheduling: false,
    approvals: { mode: "only", tools: [] },
    limits: { maxModelCalls: 4, maxOutputTokens: 12288, maxOutputTokensPerCall: 4096, summaryTokens: 256 },
    defaultModel: { provider: "LiveQualification", name: process.env.OW_LIVE_MODEL },
  });
}
async function turn(session, input) {
  const run = await session.send(input, { output: { reasoning: true } });
  const totals = { input: 0, output: 0, usageEvents: 0, reasoningCharacters: 0, textCharacters: 0, attempts: 0 };
  for await (const item of run.events()) {
    if (item.summary?.type === "reasoning_delta") totals.reasoningCharacters += item.summary.text.length;
    if (item.summary?.type === "assistant_delta") totals.textCharacters += item.summary.text.length;
    if (item.summary?.type === "model_attempt_started") totals.attempts++;
    if (item.event.type === "model_usage") {
      totals.usageEvents++;
      totals.input += item.event.data.input_tokens;
      totals.output += item.event.data.output_tokens;
    }
  }
  const result = await run.result();
  if (result.status !== "completed") {
    console.log(JSON.stringify({ status: result.status, providerError: result.providerError, totals, toolCalls }));
    throw new Error("Live conversation did not complete");
  }
  assert.ok(result.finalOutput.includes(receipt), "Final answer must use the actual tool receipt");
  assert.ok(totals.usageEvents > 0 && totals.input > 0 && totals.output > 0, "Provider usage must be reported");
  return totals;
}
try {
  const agent = await start();
  const session = await agent.newSession();
  const first = await turn(session, "Call app.lookup once for atlas.example. Then give its price and exact receipt.");
  assert.equal(toolCalls, 1);
  const reference = session.reference();
  await harness.shutdown();
  const restarted = await start();
  const second = await turn(await restarted.resumeSession(reference), "Repeat the exact receipt from the previous lookup. Do not call any tool.");
  assert.equal(toolCalls, 1, "Restart/history replay must not execute the old call");
  console.log(JSON.stringify({ model: process.env.OW_LIVE_MODEL, api: process.env.OW_LIVE_API, first, afterRestart: second, toolCalls }));
} finally {
  try { await harness?.shutdown(); }
  finally { await rm(stateDir, { recursive: true, force: true }); }
}
