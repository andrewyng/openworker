// A real managed Rust host with compiled test integrations and a scripted model.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { Harness } from "../dist/index.js";

test("observer failure allows execution and a required scanner blocks a resumed tool", {
  skip: !process.env.OW_TS_TEST_HOST, timeout: 90000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), "ow-integrations-"));
  const workspace = join(root, "workspace");
  const log = join(root, "events.jsonl");
  await mkdir(workspace);
  let harness;
  t.after(async () => { await harness?.shutdown(); await rm(root, {recursive:true, force:true}); });
  async function start(mode) {
    harness = await Harness.start({
      executable: process.env.OW_TS_TEST_HOST, stateDir: join(root,"state"),
      scripted: true, sandbox: "off", compliance: {retentionDays: 1},
      integrations: {"mock-observer": {log}, "mock-scanner": {enabled:false}},
      integrationPolicies: {"mock-scanner": mode},
    });
    return harness.createAgent({
      name: "worker", instructions: "Use the probe.", tools: ["sdk.probe"],
      approvals: {mode:"always"}, integrations: {"mock-observer":{}},
    });
  }
  const agent = await start("off-by-default");
  const request = 'TEST_RESOURCE_GRANTS [{"name":"sdk__probe","arguments":{"block":false}}]';
  const first = await agent.newSession({workspace});
  const waiting = await first.run(request);
  assert.equal(waiting.status,"interrupted");
  const approve = interruption => ({type:"approval",interactionId:interruption.id,decision:"once"});
  await first.send(approve(waiting.interruptions[0]));
  assert.equal((await first.resume()).status, "completed");
  const proof = join(workspace,"proof.txt");
  assert.equal(await readFile(proof,"utf8"), "called\n");
  await first.close();

  async function observerEvents() {
    try {
      return (await readFile(log, "utf8")).trim().split("\n").filter(Boolean).map(line=>JSON.parse(line));
    } catch(e) { if (e.code === "ENOENT") return []; throw e; }
  }
  let events=[];
  for (let i=0; i<150; i++) {
    events=(await observerEvents()).filter(e=>e.session===first.id);
    if(events.at(-1)?.state==="session_closed") break;
    await delay(50);
  }
  assert.equal(events.at(-1)?.state,"session_closed");
  for (const event of events) {
    assert.deepEqual(event.agent_context, {agent_id:first.id,parent_agent_id:null,root_agent_id:first.id});
  }
  assert.deepEqual(events.map(e=>e.seq), [...new Set(events.map(e=>e.seq))].sort((a,b)=>a-b));
  assert.deepEqual(events.filter(e=>e.interaction===waiting.interruptions[0].id).map(e=>e.state),
    ["waiting_user_approval","user_approval_done","waiting_tool_completion","tool_finished"]);
  assert.ok(events.some(e=>e.state==="tool_finished"));

  const second=await agent.newSession({workspace, integrations:{"mock-observer":{enabled:false},"mock-scanner":{enabled:false}}});
  const paused=await second.run(request);
  assert.equal(paused.status,"interrupted");
  const reference=second.reference();
  await harness.shutdown();
  await start("required");
  const resumed=await harness.resume(reference);
  await resumed.send(approve(paused.interruptions[0]));
  assert.equal((await resumed.resume()).status, "completed");
  assert.equal(await readFile(proof,"utf8"), "called\n", "resuming approval must not execute the newly blocked tool");
  const snapshot=await resumed.snapshot();
  assert.equal(snapshot.session.integrations["mock-scanner"].source,"required");
  const activity = await resumed.complianceActivity();
  assert.ok(activity.activities.some(event => event.facts.code === "FIXTURE_TOOL_BLOCKED"));
  assert.ok(!(await observerEvents()).some(e=>e.session===second.id));
});
