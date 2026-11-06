import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const plan = (name, args) => `TEST_RESOURCE_GRANTS ${JSON.stringify([{name, arguments: args}])}`;
async function invoke(h, s, name, args) {
  const run = await s.send(plan(name, args));
  assert.equal((await run.result()).status, "completed");
  const history = await h.request("history/read", {session_id: s.id, after: 0});
  const item = history.items.map(v => v.item).reverse().find(v => v.type === "function_call_output");
  const result = JSON.parse(item.output);
  assert.notEqual(result.isError, true, JSON.stringify(result));
  return result;
}

test("memory scopes, approval denial and scheduled work survive managed restart", {timeout: 180000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "ow-assistant-ts-"));
  const workspace = join(root, "work"); await mkdir(workspace);
  const options = {executable: process.env.OW_TS_TEST_HOST, stateDir: join(root, "state"), sandbox: "off", scripted: true};
  let h = await Harness.start(options);
  t.after(async () => { await h.shutdown(); await rm(root, {recursive: true, force: true}); });
  const definition = {name: "coder", instructions: "Help the learner.", memoryScope: "course/learner-a", scheduling: true, approvals: {mode: "never"}, tools: ["coding.write_file"]};
  const coder = h.createAgent(definition);
  const s = await coder.newSession({workspace});
  t.diagnostic("coder ready");
  const m = (await invoke(h, s, "memory_remember", {summary: "Theme", content: "Ocean blue"})).memory;
  t.diagnostic("memory written");
  const evaluator = h.createAgent({name: "evaluator", instructions: "Evaluate.", memoryScope: "course/learner-a", approvals: {mode: "never"}});
  const e = await evaluator.newSession({workspace});
  assert.equal((await invoke(h, e, "memory_read", {id: m.id})).memory.content, "Ocean blue");
  t.diagnostic("shared memory read");
  const other = h.createAgent({name: "other", instructions: "Other learner.", memory: true, approvals: {mode: "never"}});
  const o = await other.newSession({workspace});
  assert.deepEqual((await invoke(h, o, "memory_search", {query: ""})).memories, []);
  assert.equal((await o.state()).capabilities.timers, false);
  t.diagnostic("private scope checked");
  const guarded = h.createAgent({...definition, name: "guarded", approvals: {mode: "always"}});
  const g = await guarded.newSession({workspace});
  t.diagnostic("guarded session ready");
  const run = await g.send(plan("memory_remember", {summary: "Forbidden", content: "not approved"}));
  const result = await run.result();
  assert.equal(result.status, "interrupted");
  assert.equal(result.interruptions[0].kind, "approval");
  await g.send({type: "approval", interactionId: result.interruptions[0].id, decision: "deny"});
  assert.equal((await run.result()).status, "completed");
  assert.deepEqual((await invoke(h, e, "memory_search", {query: "Forbidden"})).memories, []);
  t.diagnostic("denial checked");
  const at = Math.floor(Date.now() / 1000) + 15;
  const task = (await invoke(h, s, "schedule_create", {run_at: at, prompt: plan("coding__write_file", {path: "scheduled.txt", content: "once", create_only: true})})).schedule;
  const state = await s.state();
  assert.equal(state.capabilities.timers, true);
  assert.deepEqual(state.timers, [{id: task.id, nextRunAt: at, remainingRuns: 1}]);
  const ref = s.reference();
  t.diagnostic("schedule created");
  await h.shutdown(); h = await Harness.start(options);
  const resumed = await h.createAgent(definition).resumeSession(ref);
  const until = Date.now() + 60000;
  while (Date.now() < until) {
    const snap = await resumed.snapshot();
    const content = await readFile(join(workspace, "scheduled.txt"), "utf8").catch(() => null);
    if (content === "once" && snap.turn?.status === "completed") break;
    assert.notEqual(snap.turn?.status, "failed", JSON.stringify(snap));
    await new Promise(r => setTimeout(r, 100));
  }
  assert.equal(await readFile(join(workspace, "scheduled.txt"), "utf8"), "once");
  assert.deepEqual((await resumed.state()).timers, []);
  assert.equal((await invoke(h, resumed, "memory_read", {id: m.id})).memory.content, "Ocean blue");
  const history = await h.request("history/read", {session_id: resumed.id, after: 0});
  assert.equal(history.items.filter(v => v.item.runtime_notice === "scheduled_task").length, 1);
  assert.equal(history.items.filter(v => v.item.name === "coding__write_file").length, 1);
});
