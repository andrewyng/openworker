import { test as nodeTest } from "node:test";
// This fault-injection host is a POSIX shebang script, not a Windows executable.
// desktop-api.test.mjs covers the real native Windows process and crash recovery.
const test = process.platform === "win32" ? nodeTest.skip : nodeTest;
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { Harness } from "../dist/index.js";

const executable = fileURLToPath(new URL("./fake-host.mjs", import.meta.url));
await chmod(executable, 0o755);
async function setup(t, extra = {}) {
  const root = await mkdtemp(join(tmpdir(), "ow-ts-unit-"));
  const h = await Harness.start({
    executable,
    stateDir: join(root, "state"),
    agents: [{ id: "assistant", instructions: "test", tools: ["test.probe"] }],
    restart: { maxRestarts: 2, delayMs: 10 },
    shutdownTimeoutMs: 100,
    ...extra,
  });
  t.after(async () => {
    await h.shutdown();
    await rm(root, { recursive: true, force: true });
  });
  return { h, root };
}
async function recovered(h, previousPid) {
  for (let n = 0; n < 300; n++) {
    if (h.state === "ready" && h.pid !== previousPid) return;
    await delay(10);
  }
  throw Error("did not recover");
}
test("recovery waits for host exit, not descendant pipe EOF", async(t)=>{
  const {h,root}=await setup(t);
  const marker=join(root,"holder.pid"); const old=h.pid;
  await assert.rejects(h.request("crash_with_inherited_pipes",{marker}),{code:"OutcomeUnknown"});
  const pid=Number(await readFile(marker,"utf8"));
  t.after(()=>{try{process.kill(pid,"SIGKILL");}catch{}});
  await recovered(h,old);
  assert.deepEqual(await h.request("echo",{ok:true}),{ok:true});
});
test("handshake, JSON framing, concurrent callers and safe environment", async (t) => {
  process.env.OW_TS_SECRET = "not-inherited";
  process.env.OPENAI_API_KEY = "not-inherited-either";
  t.after(() => {
    delete process.env.OW_TS_SECRET;
    delete process.env.OPENAI_API_KEY;
  });
  const { h } = await setup(t);
  assert.equal(h.capabilities.agent_runs, true);
  assert.deepEqual(await h.request("env", {}), {
    secret: null,
    modelKey: null,
  });
  assert.deepEqual(
    await Promise.all(
      Array.from({ length: 20 }, (_, n) => h.request("echo", { n })),
    ),
    Array.from({ length: 20 }, (_, n) => ({ n })),
  );
  const explicit = await setup(t, { inheritEnv: ["OW_TS_SECRET"] });
  assert.equal((await explicit.h.request("env", {})).secret, "not-inherited");
});
test("dynamic agent handles fail clearly against older hosts", async (t) => {
  const { h, root } = await setup(t);
  await assert.rejects(h.session("assistant", { autoSkipQuestions: true }), { code: "Setup" });
  const agent = h.createAgent({
    name: "builder",
    instructions: "Build.",
    tools: ["game.preview"],
  });
  await assert.rejects(agent.ready(), { code: "Setup" });
  await assert.rejects(agent.newSession({ workspace: root }), {
    code: "Setup",
  });
  assert.equal(h.state, "ready");
});
test("journal hints are best-effort, filtered, removable and observer failures are contained", async (t) => {
  const { h } = await setup(t);
  const hints = [];
  const stop = h.onJournalHint((method, params) =>
    hints.push({ method, params }),
  );
  h.onJournalHint(() => {
    throw Error("application observer failure");
  });
  await h.request("hints", {});
  assert.deepEqual(
    hints.map((x) => x.method),
    ["session/event", "stream/gap"],
  );
  stop();
  await h.request("hints", {});
  assert.equal(hints.length, 2);
  assert.equal(h.state, "ready");
});
test("lost mutation is OutcomeUnknown, is never replayed, and next process works", async (t) => {
  const { h, root } = await setup(t);
  const pid = h.pid;
  const states = [];
  const unsubscribe = h.onStateChange((state) => states.push(state));
  const marker = join(root, "effects");
  const first = h.request("crash", { marker });
  const queued = h.request("echo", { queued: true });
  await Promise.all([
    assert.rejects(first, { code: "OutcomeUnknown" }),
    assert.rejects(queued, { code: "Unavailable" }),
  ]);
  await recovered(h, pid);
  assert.deepEqual(states, ["recovering", "ready"]);
  unsubscribe();
  assert.equal(await readFile(marker, "utf8"), "called\n");
  assert.deepEqual(await h.request("echo", { alive: true }), { alive: true });
});
test("invalid framing, oversized output and timeout trigger bounded recovery", async (t) => {
  for (const method of ["malformed", "huge", "hang"]) {
    const { h } = await setup(t, { requestTimeoutMs: 200 });
    const pid = h.pid;
    await assert.rejects(h.request(method, {}), { code: "OutcomeUnknown" });
    await recovered(h, pid);
  }
});
test("restart budget is finite; shutdown is idempotent", async (t) => {
  const { h } = await setup(t, { restart: { maxRestarts: 0 } });
  await assert.rejects(h.request("malformed", {}), { code: "OutcomeUnknown" });
  await delay(50);
  await assert.rejects(h.ready(), { code: "Unavailable" });
  await h.shutdown();
  await h.shutdown();
  await assert.rejects(h.request("echo", {}), { code: "Closed" });
});
test("handshake mismatch rejects; invalid input and remote diagnostics are safe", async (t) => {
  await assert.rejects(
    Harness.start({
      executable,
      stateDir: "/tmp/unused",
      agents: [{ id: "a", instructions: "bad-version", tools: ["x.y"] }],
      shutdownTimeoutMs: 100,
    }),
    { code: "Setup" },
  );
  const { h } = await setup(t);
  await assert.rejects(
    h.request("error", {}),
    (e) => e.code === "Remote" && !e.message.includes("sk-"),
  );
  await assert.rejects(h.request("echo", { text: "x".repeat(1024 * 1024) }), {
    code: "InvalidInput",
  });
  assert.equal(h.state, "ready");
});
test("stream drains all pages after terminal status and checkpoints partial consumption", async (t) => {
  const { h } = await setup(t);
  const s = await h.resume({ session_id: "s", agent: "assistant" });
  const stream = await s.runStreamed("hello", { submissionId: "stable-id" });
  assert.equal(stream.checkpoint().submissionId, "stable-id");
  for await (const event of stream) {
    assert.equal(event.event.event_id, 1);
    assert.equal(stream.checkpoint().cursors.s, 1);
    break;
  }
  const rest = [];
  for await (const e of stream) rest.push(e.event.event_id);
  assert.deepEqual(rest, [2, 3, 4, 5]);
  assert.equal((await stream.result()).finalOutput, "done");
  const replay = await s.resumeStreamed({
    submissionId: "stable-id",
    cursors: { s: 1 },
  });
  const ids = [];
  for await (const e of replay) ids.push(e.event.event_id);
  assert.deepEqual(ids, [2, 3, 4, 5]);
});
test("partial member errors and invalid approval/question responses are not hidden", async (t) => {
  const { h } = await setup(t);
  const s = await h.resume({ session_id: "s", agent: "assistant" });
  await assert.rejects(s.cancel(), { code: "Remote" });
  await assert.rejects(s.close(), { code: "Remote" });
  await assert.rejects(
    s.respond(
      { id: "i", session_id: "s", agent: "assistant", kind: "approval" },
      { answer: "yes" },
    ),
    { code: "InvalidInput" },
  );
  await assert.rejects(
    s.respond(
      { id: "i", session_id: "other", kind: "approval" },
      { decision: "once" },
    ),
    // The authoritative live roster rejects this, rather than a stale client
    // SessionRef rejecting every newly spawned member.
    { code: "Remote" },
  );
});
test("AbortSignal stops waiting, never implicitly cancels an agent", async (t) => {
  const { h } = await setup(t);
  const s = await h.resume({ session_id: "s", agent: "assistant" });
  await assert.rejects(
    s.run("hello", {
      signal: AbortSignal.abort(),
      submissionId: "cancel-test",
    }),
    (e) => e.code === "Aborted" && e.context.submissionId === "cancel-test",
  );
});
test("safe reads recover transparently after an in-flight process death", async (t) => {
  const { h } = await setup(t);
  assert.deepEqual(
    await h.read("session/attach", { session_id: "crash-once" }),
    { cursor: 0 },
  );
});
test("read recovery cannot be used for mutations; a full queue never busy-spins", async (t) => {
  const { h } = await setup(t);
  await assert.rejects(h.read("run/start", {}), { code: "InvalidInput" });
  const queued = Array.from({ length: 64 }, (_, n) => h.request("echo", { n }));
  await assert.rejects(h.read("session/attach", { session_id: "s" }), {
    code: "Unavailable",
  });
  await Promise.all(queued);
  assert.equal(h.state, "ready");
});
