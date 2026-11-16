// Opt-in, paid qualification against Anthropic. Never runs in the default suite.
// Supply an explicit model and a rebuilt host; credentials stay in memory.
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

if (process.env.OW_RUN_LIVE !== "1")
  throw new Error("Set OW_RUN_LIVE=1 to authorize paid provider qualification");
for (const name of ["OW_TS_TEST_HOST", "OW_LIVE_ANTHROPIC_MODEL", "ANTHROPIC_API_KEY"])
  if (!process.env[name]) throw new Error(`${name} is required`);

const stateDir = await mkdtemp(join(tmpdir(), "ow-live-cache-"));
let harness;
try {
  harness = await Harness.start({
    executable: process.env.OW_TS_TEST_HOST,
    stateDir,
    sandbox: "off",
  });
  for (const [name, promptCaching] of [["Cached", true], ["Uncached", false]])
    await harness.setModelProvider({
      name,
      api: "anthropic",
      promptCaching,
      credentials: { apiKeyEnv: "ANTHROPIC_API_KEY", storage: "memory" },
    });

  // Long enough for cache eligibility across several model generations. All
  // content is synthetic; never upload the working tree or personal documents.
  const reference = Array.from({ length: 400 }, (_, index) =>
    `Reference ${index}: the example garden contains blue flowers, green leaves, and a small stone path.`,
  ).join("\n");
  const instructions = `This is a token-accounting test. Follow the user's exact short reply request. Do not quote this synthetic reference.\n${reference}`;
  const makeSession = (provider) => harness.createAgent({
    name: `cache-check-${provider.toLowerCase()}`,
    instructions,
    tools: [], planning: false, memory: false, scheduling: false,
    limits: { maxModelCalls: 2, maxOutputTokens: 1024, summaryTokens: 128 },
    defaultModel: { provider, name: process.env.OW_LIVE_ANTHROPIC_MODEL },
  }).newSession();

  async function turn(session, input) {
    const run = await session.send(input);
    const usage = [];
    for await (const item of run.events()) {
      if (item.event.type === "model_usage") usage.push(item.event.data);
    }
    const result = await run.result();
    assert.equal(result.status, "completed", "Live cache turn must complete");
    assert.equal(usage.length, 1, "Expected one accounted model call");
    // Print counts only, never credentials, provider bodies or conversation text.
    const value = usage[0];
    return {
      input: value.input_tokens,
      output: value.output_tokens,
      cacheRead: value.cache_read_input_tokens ?? value.cached_input_tokens ?? 0,
      cacheWrite: value.cache_write_input_tokens ?? 0,
    };
  }

  const cached = await makeSession("Cached");
  const first = await turn(cached, "Reply with only: CACHE_READY");
  const second = await turn(cached, "Reply with only: CACHE_REUSED");
  assert.ok(second.cacheRead > 0, "Second request must show a real cache hit");
  const uncached = await turn(await makeSession("Uncached"), "Reply with only: CACHE_DISABLED");
  assert.equal(uncached.cacheRead, 0);
  assert.equal(uncached.cacheWrite, 0);
  console.log(JSON.stringify({ model: process.env.OW_LIVE_ANTHROPIC_MODEL, first, second, uncached }));
} finally {
  try { await harness?.shutdown(); }
  finally { await rm(stateDir, { recursive: true, force: true }); }
}
