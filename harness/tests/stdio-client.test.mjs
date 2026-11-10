import test from "node:test";
import assert from "node:assert/strict";
import { RuntimeClient } from "./support/runtime.mjs";
test("invalid UTF-8 and trailing notifications after a malformed frame are rejected", async (t) => {
  for (const source of [
    `process.stdin.once('data',()=>process.stdout.write(Buffer.from([255,10])))`,
    `process.stdin.once('data',()=>process.stdout.write('invalid\\n'+JSON.stringify({jsonrpc:'2.0',method:'session/event',params:{}})+'\\n'))`,
  ]) {
    const c = client(t, source);
    let hints = 0;
    c.on("event", () => hints++);
    await assert.rejects(c.request("probe", {}));
    assert.equal(hints, 0);
    assert.equal(c.alive, false);
  }
});
function client(t, source) {
  const c = new RuntimeClient(process.execPath, [
    "--input-type=module",
    "-e",
    source,
  ]);
  t.after(() => c.stop());
  return c;
}
test("direct stdio rejects malformed responses and bounds output", async (t) => {
  for (const frame of [
    "not-json",
    '{"jsonrpc":"1.0","id":1,"result":{}}',
    '{"jsonrpc":"2.0","id":1,"result":{},"error":{}}',
    "x".repeat(1024 * 1024 + 1),
  ]) {
    const source =
      frame.length > 1024
        ? `process.stdin.once('data',()=>process.stdout.write('x'.repeat(1024*1024+1)))`
        : `process.stdin.once('data',()=>console.log(${JSON.stringify(frame)}))`;
    const c = client(t, source);
    await assert.rejects(c.request("probe", {}));
    assert.equal(c.alive, false);
  }
});
test("direct stdio sanitizes remote diagnostics and does not inherit model keys", async (t) => {
  const prior = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "synthetic-no-inherit";
  t.after(() => {
    if (prior === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = prior;
  });
  const c = client(
    t,
    `process.stdin.once('data',()=>console.log(JSON.stringify({jsonrpc:'2.0',id:1,result:{key:process.env.OPENAI_API_KEY??null}})))`,
  );
  assert.deepEqual(await c.request("probe", {}), { key: null });
  const bad = client(
    t,
    `process.stdin.once('data',()=>console.log(JSON.stringify({jsonrpc:'2.0',id:1,error:{message:'synthetic-secret'}})))`,
  );
  await assert.rejects(
    bad.request("probe", {}),
    (e) => !e.message.includes("synthetic-secret"),
  );
});
test("direct stdio disconnect rejects all callers without replay and bounds input/queue", async (t) => {
  const c = client(t, `process.stdin.once('data',()=>process.exit(1))`);
  await assert.rejects(
    c.request("probe", { text: "x".repeat(1024 * 1024) }),
    /frame limit/,
  );
  const requests = Array.from({ length: 64 }, () => c.request("mutation", {}));
  await assert.rejects(c.request("extra", {}), /queue is full/);
  const settled = await Promise.allSettled(requests);
  assert.ok(settled.every((v) => v.status === "rejected"));
  assert.equal(c.alive, false);
  await assert.rejects(c.request("mutation", {}), /disconnected/);
});
