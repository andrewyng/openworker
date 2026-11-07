import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Harness } from "../dist/index.js";

test(
  "desktop API: app callback, approval, send/events/result, state and restart",
  { timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-desktop-ts-"));
    const options = {
      executable: process.env.OW_TS_TEST_HOST,
      stateDir: join(root, "state"),
      sessions: { directory: join(root, "sessions") },
      scratch: { directory: join(root, "scratch") },
      sandbox: "off",
      scripted: true,
    };
    let h = await Harness.start(options);
    t.after(async () => {
      await h.shutdown();
      await rm(root, { recursive: true, force: true });
    });
    let calls = 0;
    const tools = {
      namespace: "app",
      tools: {
        preview: {
          description: "Show preview.",
          inputSchema: {
            type: "object",
            properties: {},
            additionalProperties: false,
          },
          execute: async (_, context) => {
            calls++;
            await context.progress("Ready");
            return { shown: true };
          },
        },
      },
    };
    await h.registerTools(tools);
    const definition = {
      name: "builder",
      instructions: "Show preview.",
      tools: ["app.preview"],
    };
    const agent = h.createAgent(definition);
    await agent.ready();
    await mkdir(join(root, "work"));
    const preview = await h.request("policy/resolve", {
      coworker: "builder",
      workspace: join(root, "work"),
    });
    assert.equal(typeof preview.policy.digest, "string");
    await assert.rejects(
      h.request("policy/resolve", {
        coworker: "not-registered",
        workspace: root,
      }),
    );
    const session = await agent.newSession();
    assert.equal((await session.state()).status, "idle");
    const prompt =
      "TEST_RESOURCE_GRANTS " +
      JSON.stringify([{ name: "app__preview", arguments: {} }]);
    const run = await session.send({ text: prompt });
    const result = await run.result();
    assert.equal(result.status, "interrupted");
    assert.equal(calls, 0);
    assert.equal((await session.state()).status, "waiting");
    await assert.rejects(session.send({ text: "yes", decision: "once" }));
    await session.send({
      type: "approval",
      interactionId: result.interruptions[0].id,
      decision: "session",
    });
    assert.equal((await run.result()).status, "completed");
    assert.equal(calls, 1);
    let events = 0;
    for await (const e of run.events()) {
      assert.ok(e.event);
      events++;
    }
    assert.ok(events > 0);
    const ref = session.reference();
    await h.shutdown();
    h = await Harness.start(options);
    await h.registerTools(tools);
    const restored = await h.createAgent(definition).resumeSession(ref);
    assert.equal((await restored.state()).status, "idle");
    assert.equal(
      (await (await restored.send(prompt)).result()).status,
      "completed",
    );
    assert.equal(calls, 2);
  },
);

test(
  "child crash during callback is not replayed; retained callbacks rebind",
  { timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-desktop-crash-"));
    const h = await Harness.start({
      executable: process.env.OW_TS_TEST_HOST,
      stateDir: join(root, "state"),
      sandbox: "off",
      scripted: true,
    });
    t.after(async () => {
      await h.shutdown();
      await rm(root, { recursive: true, force: true });
    });
    let calls = 0;
    await h.registerTools({
      namespace: "app",
      tools: {
        effect: {
          description: "Fixture effect.",
          inputSchema: {
            type: "object",
            properties: { block: { type: "boolean" } },
            required: ["block"],
            additionalProperties: false,
          },
          execute: async ({ block }, context) => {
            calls++;
            if (block)
              await new Promise((resolve) => {
                if (context.signal.aborted) resolve();
                else
                  context.signal.addEventListener("abort", resolve, {
                    once: true,
                  });
              });
            return { done: true };
          },
        },
      },
    });
    const agent = h.createAgent({
      name: "worker",
      instructions: "Use effect.",
      tools: ["app.effect"],
      approvals: { mode: "never" },
    });
    const session = await agent.newSession();
    const prompt = (block) =>
      "TEST_RESOURCE_GRANTS " +
      JSON.stringify([{ name: "app__effect", arguments: { block } }]);
    const run = await session.send(prompt(true));
    const wait = async (predicate) => {
      for (let i = 0; i < 1000; i++) {
        if (predicate()) return;
        await new Promise((r) => setTimeout(r, 20));
      }
      throw Error("state did not converge");
    };
    await wait(() => calls === 1);
    const old = h.pid;
    process.kill(old, "SIGKILL");
    await wait(() => h.state === "ready" && h.pid !== old);
    assert.equal((await run.result()).status, "failed");
    assert.equal(calls, 1);
    assert.equal(
      (await (await session.send(prompt(false))).result()).status,
      "completed",
    );
    assert.equal(calls, 2);
  },
);
