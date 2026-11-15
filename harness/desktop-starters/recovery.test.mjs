import { test } from "node:test";
import assert from "node:assert/strict";
import { recoverProject } from "./templates/electron-game-builder/electron/recovery.mjs";
import { Harness } from "../typescript-sdk/dist/index.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
test("history projection paginates, excludes private reasoning, and restores only confirmed previews", async () => {
  const items = Array.from({ length: 120 }, (_, i) => ({
    role: "user",
    content: `message-${i}`,
  }));
  items.push({
    type: "function_call",
    name: "game__show_preview",
    call_id: "good",
    arguments: JSON.stringify({ html: "approved" }),
  });
  items.push({
    type: "function_call_output",
    call_id: "good",
    output: JSON.stringify({
      isError: false,
      structuredContent: { shown: true },
    }),
  });
  items.push({
    type: "function_call",
    name: "game__show_preview",
    call_id: "bad",
    arguments: { html: "denied" },
  });
  items.push({
    type: "function_call_output",
    call_id: "bad",
    output: { isError: true },
  });
  items.push({ type: "reasoning", content: "private" });
  items.push({
    type: "function_call",
    name: "game__show_preview",
    call_id: "pending",
    arguments: { html: "unconfirmed" },
  });
  const h = {
    request: async (method, p) =>
      method === "provider/list"
        ? {
            connections: [
              {
                id: "Pinned",
                api: "responses",
                base_url: "https://api.openai.com/v1",
              },
            ],
          }
        : {
            items: items
              .slice(p.after, p.after + 50)
              .map((item, i) => ({ index: p.after + i, item })),
            cursor: Math.min(items.length, p.after + 50),
            total_items: items.length,
          },
  };
  const session = {
    id: "session",
    snapshot: async () => ({
      session: {
        model_selection: { connection: "Pinned", model: "original-model" },
      },
    }),
  };
  const view = await recoverProject(h, session, { provider: "Wrong" });
  assert.equal(view.messages.length, 120);
  assert.equal(view.messages.at(-1), "message-119");
  assert.equal(view.html, "approved");
  assert.equal(view.settings.model, "original-model");
});
test(
  "real SDK recovery never replays a preview and preserves denial and model identity",
  { skip: !process.env.OW_TS_TEST_HOST, timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-recovery-"));
    const options = {
      executable: process.env.OW_TS_TEST_HOST,
      stateDir: root,
      sandbox: "off",
      scripted: true,
    };
    let h = await Harness.start(options);
    let effects = 0;
    const tools = {
      namespace: "game",
      tools: {
        show_preview: {
          description: "Show preview.",
          inputSchema: {
            type: "object",
            properties: { html: { type: "string" } },
            required: ["html"],
            additionalProperties: false,
          },
          execute: async () => {
            effects++;
            return { shown: true };
          },
        },
      },
    };
    const def = {
      name: "builder",
      instructions: "Use preview.",
      tools: ["game.show_preview"],
      defaultModel: { provider: "Local", name: "fixture" },
    };
    t.after(async () => {
      await h.shutdown();
      await rm(root, { recursive: true, force: true });
    });
    await h.setModelProvider({
      name: "Local",
      api: "chat-completions",
      baseUrl: "http://127.0.0.1:1/v1",
    });
    await h.registerTools(tools);
    let session = await h.createAgent(def).newSession();
    const send = (html) =>
      session.send(
        "TEST_RESOURCE_GRANTS " +
          JSON.stringify([{ name: "game__show_preview", arguments: { html } }]),
      );
    const run = await send("approved-game");
    const waiting = await run.result();
    assert.equal((await recoverProject(h, session)).html, "");
    await session.send({
      type: "approval",
      interactionId: waiting.interruptions[0].id,
      decision: "once",
    });
    assert.equal((await run.result()).status, "completed");
    assert.equal(effects, 1);
    const before = await recoverProject(h, session);
    const ref = session.reference();
    await h.shutdown();
    h = await Harness.start(options);
    await h.registerTools(tools);
    session = await h.createAgent(def).resumeSession(ref);
    assert.deepEqual(await recoverProject(h, session), before);
    assert.equal(effects, 1);
    const denied = await send("denied-game");
    const pending = await denied.result();
    await session.send({
      type: "approval",
      interactionId: pending.interruptions[0].id,
      decision: "deny",
    });
    await denied.result();
    assert.equal((await recoverProject(h, session)).html, "approved-game");
    assert.equal(effects, 1);
  },
);
