// Opt-in billable test. Only a synthetic game prompt and its generated HTML go to OpenAI.
// OPENAI_API_KEY (or OW_LIVE_CREDENTIAL_FILE containing that assignment) is never logged.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Harness } from "../typescript-sdk/dist/index.js";
import { recoverProject } from "./templates/electron-game-builder/electron/recovery.mjs";
if (process.env.OW_LIVE_ACCEPT !== "1")
  throw Error("Explicit OW_LIVE_ACCEPT=1 required for billable model calls");
let apiKey = process.env.OPENAI_API_KEY;
if (!apiKey && process.env.OW_LIVE_CREDENTIAL_FILE) {
  const text = await readFile(process.env.OW_LIVE_CREDENTIAL_FILE, "utf8");
  apiKey = text
    .match(/^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.+)\s*$/m)?.[1]
    ?.trim()
    .replace(/^(['"])(.*)\1$/, "$2");
}
if (!apiKey) throw Error("Approved OpenAI credential is required");
const root = await mkdtemp(join(tmpdir(), "ow-game-live-"));
const stateDir = process.env.OW_LIVE_STATE_DIR ?? join(root, "state");
const model = process.env.OW_LIVE_MODEL ?? "gpt-6-sol";
const options = {
  executable: process.env.OPENWORKER_EXECUTABLE,
  stateDir,
  sandbox: "off",
};
let h,
  session,
  effects = 0;
const tools = {
  namespace: "game",
  tools: {
    show_preview: {
      description:
        "Display a self-contained playable HTML game in the isolated preview.",
      inputSchema: {
        type: "object",
        properties: { html: { type: "string", maxLength: 100000 } },
        required: ["html"],
        additionalProperties: false,
      },
      execute: async ({ html }, context) => {
        assert.equal(context.signal.aborted, false);
        assert.equal(typeof html, "string");
        effects++;
        await writeFile(join(root, `game-${effects}.html`), html, {
          mode: 0o600,
        });
        return { shown: true };
      },
    },
  },
};
const definition = {
  name: "arcade-builder-v1",
  instructions:
    "Build playful, polished self-contained HTML/CSS/JavaScript games. Call game.show_preview with complete HTML. No external assets or network calls. Iterate on the same game when asked; do not claim it was shown before the tool succeeds.",
  tools: ["game.show_preview"],
};
async function start(ref) {
  h = await Harness.start(options);
  console.log("Harness ready; configuring memory-only model provider.");
  await h.setModelProvider({
    name: "LiveOpenAI",
    api: "responses",
    credentials: { apiKey, storage: "memory" },
  });
  console.log("Provider configured; registering preview callback.");
  await h.registerTools(tools);
  if (process.env.OW_LIVE_EXISTING_AGENT === "1") {
    session = ref
      ? await h.resume(ref)
      : await h.session(definition.name, {
          model: { provider: "LiveOpenAI", name: model },
        });
  } else {
    const agent = h.createAgent(definition);
    session = ref
      ? await agent.resumeSession(ref)
      : await agent.newSession({
          model: { provider: "LiveOpenAI", name: model },
        });
  }
}
async function finish(run) {
  for (let n = 0; n < 3; n++) {
    const result = await run.result();
    if (result.status === "completed") return result;
    assert.equal(result.status, "interrupted");
    assert.equal(result.interruptions.length, 1);
    const i = result.interruptions[0];
    assert.equal(i.kind, "approval");
    assert.match(i.tool, /show_preview$/);
    await session.send({
      type: "approval",
      interactionId: i.id,
      decision: "once",
    });
  }
  throw Error("Model exceeded bounded preview approvals");
}
try {
  await start();
  console.log(
    "Live game test started; credentials stay in memory. Evidence:",
    root,
  );
  const run = await session.send(
    "Build a beautiful small game called Neon Catch. A big accessible button labeled Catch star increments a visible score, with a Reset button. Use a dark navy background, cyan and violet highlights, a large illustrated star in inline SVG or CSS, and concise instructions. One self-contained HTML under 12000 characters. Call the preview tool exactly once and then give a one-sentence summary. No external requests, dependencies or timers.",
  );
  const waiting = await run.result();
  assert.equal(waiting.status, "interrupted");
  assert.equal(effects, 0);
  assert.equal((await recoverProject(h, session)).html, "");
  const approval = waiting.interruptions[0];
  const ref = session.reference();
  await h.shutdown();
  await start(ref);
  const restored = await session.resume();
  assert.equal(restored.interruptions[0].id, approval.id);
  assert.equal(effects, 0);
  await session.send({
    type: "approval",
    interactionId: approval.id,
    decision: "once",
  });
  assert.equal((await session.resume()).status, "completed");
  assert.equal(effects, 1);
  const initial = await recoverProject(h, session);
  assert.ok(initial.html.includes("Catch star"));
  const change = await session.send(
    "Keep Neon Catch and change Catch star so each click adds 5 points instead of 1. Add a visible +5 badge beside the score. Keep Reset and the same style. Call the preview tool once, then briefly explain the change.",
  );
  await finish(change);
  assert.equal(effects, 2);
  const updated = await recoverProject(h, session);
  assert.notEqual(updated.html, initial.html);
  assert.ok(updated.messages.length >= 4);
  const finalRef = session.reference();
  await h.shutdown();
  await start(finalRef);
  assert.deepEqual(await recoverProject(h, session), updated);
  assert.equal(effects, 2);
  assert.equal((await session.state()).status, "idle");
  await writeFile(
    join(root, "session.json"),
    JSON.stringify(finalRef, null, 2),
    { mode: 0o600 },
  );
  await writeFile(
    join(root, "report.json"),
    JSON.stringify(
      {
        passed: true,
        model,
        stateDir,
        reference: finalRef,
        toolEffects: effects,
        conversationMessages: updated.messages.length,
        checks: [
          "live Responses tool call",
          "approval blocks preview",
          "pending approval identity survives restart",
          "Allow Once",
          "live iteration",
          "canonical preview/transcript/model restored without tool replay",
        ],
        uiValidated: false,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS: live build, restart approval, iteration, read-only recovery.",
    root,
  );
} finally {
  await h?.shutdown();
  apiKey = undefined;
}
