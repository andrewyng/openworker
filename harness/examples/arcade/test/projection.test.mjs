import test from "node:test";
import assert from "node:assert/strict";
import {
  EventProjection,
  projectMessages,
} from "../build/electron/event-projection.js";
test("display projection deduplicates and separates attempts without reasoning", () => {
  const p = new EventProjection();
  let id = 0;
  const event = (type, data) => ({
    event: { event_id: ++id, type, data },
    summary: null,
  });
  p.consume(event("model_attempt_started", { attempt_id: "a" }));
  const delta = event("assistant_delta", { attempt_id: "a", text: "first" });
  p.consume(delta);
  p.consume(delta);
  assert.equal(p.partial, "first");
  p.consume(event("reasoning_delta", { text: "private" }));
  assert.equal(p.partial, "first");
  p.consume(
    event("model_attempt_finished", { attempt_id: "a", status: "failed" }),
  );
  assert.equal(p.partial, "");
  p.consume(event("model_attempt_started", { attempt_id: "b" }));
  p.consume(event("assistant_delta", { attempt_id: "a", text: "late" }));
  assert.equal(p.partial, "");
  p.consume(event("assistant_delta", { attempt_id: "b", text: "second" }));
  assert.equal(p.partial, "second");
  p.finish("completed");
  assert.equal(p.partial, "");
});
test("canonical transcript shows ordinary text, not tool code, context or reasoning", () => {
  assert.deepEqual(
    projectMessages([
      {
        index: 0,
        item: {
          role: "user",
          content: JSON.stringify({
            arcade_context: { version: 1 },
            user_message: "Make a game",
          }),
        },
      },
      {
        index: 1,
        item: {
          role: "assistant",
          content: [
            { type: "reasoning", text: "secret" },
            { type: "output_text", text: "Ready" },
          ],
        },
      },
      { index: 2, item: { type: "function_call", arguments: "code" } },
      {
        index: 3,
        item: { role: "user", runtime_notice: true, content: "internal" },
      },
    ]),
    [
      { id: "history-0", role: "user", text: "Make a game" },
      { id: "history-1", role: "assistant", text: "Ready" },
    ],
  );
});

test("a safety interruption cannot be submitted as an approval or question answer", async () => {
  const { AgentService } = await import("../build/electron/agent-service.js");
  const pending = { id: "safety-1", session_id: "session-1", kind: "safety", code: "scanner_unavailable" };
  const calls = [];
  let stateReads = 0;
  const service = Object.create(AgentService.prototype);
  Object.assign(service, {
    pending: new Map([["game-1", [pending]]]), watches: new Map(),
    activeGameId: "game-1", launching: false,
    session: async () => ({
      id: "session-1", state: async () => { stateReads++; return { pending: [pending] }; },
      send: async (...args) => calls.push(["send", ...args]),
      resumeStreamed: async () => calls.push(["resume"]),
    }),
    emit: () => {},
  });
  for (const [decision, answer] of [["once", undefined], [undefined, "continue"]]) {
    // App errors remain sanitized; the pending card explains the safety pause.
    await assert.rejects(service.respond("game-1", "safety-1", decision, answer), /could not complete/i);
    assert.deepEqual(service.pending.get("game-1"), [pending]);
    assert.deepEqual(calls, []);
  }
  assert.equal(stateReads, 2);
});
