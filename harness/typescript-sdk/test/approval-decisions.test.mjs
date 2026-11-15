// Actual managed host with a deterministic local provider. No model credentials.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness, guard } from "../dist/index.js";

test("approval decisions, denial context, human fallback, grants and replay", {
  skip: !process.env.OW_TS_TEST_HOST, timeout: 180000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), "ow-approval-events-"));
  let verdict = "allow", effects = 0, fixtureError;
  const working = [], reviews = [];
  const server = createServer(async (req, res) => {
    try {
      let raw = ""; for await (const c of req) raw += c;
      const body = JSON.parse(raw);
      const reviewer = body.model === "judge";
      (reviewer ? reviews : working).push(body);
      const tool = !reviewer && body.messages.at(-1).role === "user";
      const delta = tool
        ? {tool_calls:[{index:0,id:`call-${working.length}`,type:"function",function:{name:"app__probe",arguments:"{}"}}]}
        : {content: reviewer ? JSON.stringify({verdict,reason:"This action contradicts the requested task."}) : "Done."};
      res.writeHead(200, {"Content-Type":"text/event-stream"});
      res.end(`data: ${JSON.stringify({id:"fixture",choices:[{index:0,delta,finish_reason:tool?"tool_calls":"stop"}]})}\n\ndata: ${JSON.stringify({choices:[],usage:{prompt_tokens:60,completion_tokens:20}})}\n\ndata: [DONE]\n\n`);
    } catch (error) { fixtureError = error; res.writeHead(500); res.end(); }
  });
  await new Promise(r => server.listen(0, "127.0.0.1", r));
  let h;
  t.after(async () => {
    await h?.shutdown(); server.closeAllConnections();
    await new Promise(r => server.close(r)); await rm(root, {recursive:true,force:true});
    assert.equal(fixtureError, undefined);
  });
  async function start() {
    h = await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:"off"});
    await h.setModelProvider({name:"Fixture",api:"chat-completions",baseUrl:`http://127.0.0.1:${server.address().port}/v1`});
    await h.registerTools({namespace:"app",tools:{probe:{description:"Read a synthetic value.",inputSchema:{type:"object",properties:{},additionalProperties:false},execute:()=>{effects++;return {ok:true};}}}});
    return h.createAgent({name:"worker",instructions:"Use probe once.",tools:["app.probe"],builtinTools:{askUser:false,readHistory:false},defaultModel:{provider:"Fixture",name:"worker"},approvals:{mode:"auto",reviewerModel:{provider:"Fixture",name:"judge"}}});
  }
  let agent = await start();
  for (const value of ["allow", "deny", "unsure"]) {
    verdict = value;
    const before = effects;
    const session = await agent.newSession();
    const run = await session.send("Read the value.");
    const result = await run.result();
    if (value === "unsure") {
      assert.equal(result.status, "interrupted");
      assert.equal(effects, before);
      await session.send({type:"approval",interactionId:result.interruptions[0].id,decision:"once"});
    }
    assert.equal((await run.result()).status, "completed");
    assert.equal(effects, before + (value === "deny" ? 0 : 1));
    const events = []; for await (const item of run.events()) events.push(item);
    const decisions = events.filter(e => e.approvalDecision);
    assert.equal(decisions[0].approvalDecision.verdict, value);
    assert.equal(decisions[0].approvalDecision.origin, "reviewer");
    assert.equal(decisions[0].approvalDecision.tool, "app__probe");
    assert.match(decisions[0].approvalDecision.callId, /^call-\d+$/);
    assert.ok(decisions[0].approvalDecision.interactionId);
    assert.equal(decisions[0].sessionId, session.id);
    if (value === "unsure") {
      assert.equal(decisions[1].approvalDecision.origin, "human");
      assert.equal(decisions[1].approvalDecision.verdict, "allow");
    }
    if (value === "deny") {
      const output = JSON.parse(working.at(-1).messages.find(m => m.role === "tool").content);
      assert.equal(output.executed, false);
      assert.equal(output.reason, "This action contradicts the requested task.");
      assert.match(output.message, /Do not retry/);
    }
    const replay = []; for await (const item of run.events()) replay.push(item);
    assert.deepEqual(replay, events);
    if (value === "deny") {
      const ref = session.reference();
      const reviewCount = reviews.length;
      await h.shutdown();
      agent = await start();
      const restored = await agent.resumeSession(ref);
      const duplicate = await restored.send("Read the value.", {submissionId:result.submissionId});
      assert.equal((await duplicate.result()).status, "completed");
      const recovered = []; for await (const item of duplicate.events()) recovered.push(item);
      assert.deepEqual(recovered, events);
      assert.equal(reviews.length, reviewCount, "recovery does not repeat review");
      assert.equal(effects, before, "denied callback stays unexecuted after restart");
    }
  }
  verdict = "deny";
  const protectedRun = await (await agent.newSession()).send("Read the value.", {
    guardrails:{modelOutput:[guard.regex({name:"protect",pattern:"never-match"})]},
  });
  assert.equal((await protectedRun.result()).status, "completed");
  for await (const item of protectedRun.events()) {
    if (item.approvalDecision) assert.equal(item.approvalDecision.reason, undefined);
  }
  const human = h.createAgent({name:"human",instructions:"Use probe.",tools:["app.probe"],defaultModel:{provider:"Fixture",name:"worker"},approvals:{mode:"always"}});
  const session = await human.newSession();
  const denied = await session.send("Read the value.");
  const interruption = (await denied.result()).interruptions[0];
  await session.send({type:"approval",interactionId:interruption.id,decision:"deny"});
  assert.equal((await denied.result()).status, "completed");
  const humanEvents = []; for await (const item of denied.events()) humanEvents.push(item);
  assert.equal(humanEvents.find(e => e.approvalDecision)?.approvalDecision.verdict, "deny");
  assert.equal(JSON.parse(working.at(-1).messages.findLast(m => m.role === "tool").content).reason, "The user declined this action.");
  const grantRun = await session.send("Read once more.");
  const grantResult = await grantRun.result();
  assert.equal(grantResult.status, "interrupted", JSON.stringify(grantResult));
  const pending = grantResult.interruptions[0];
  await session.send({type:"approval",interactionId:pending.id,decision:"session"});
  assert.equal((await grantRun.result()).status, "completed");
  const granted = await session.send("Read again.");
  assert.equal((await granted.result()).status, "completed");
  const grantEvents = []; for await (const item of granted.events()) grantEvents.push(item);
  assert.equal(grantEvents.find(e => e.approvalDecision)?.approvalDecision.origin, "session_grant");
  assert.equal(reviews.length, 4);
});
