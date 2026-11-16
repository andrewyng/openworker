import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

// Actual managed Rust process, deterministic HTTP/SSE provider, no live key.
test("application guidance: reviewer requests, overrides, clearing and durable restart", {skip:!process.env.OW_TS_TEST_HOST,timeout:180000}, async t => {
  const root=await mkdtemp(join(tmpdir(),"ow-guidance-"));
  const reviews=[],working=[];
  let fixtureError,effects=0;
  const server=createServer(async(req,res)=>{
    try {
      let raw="";for await(const c of req)raw+=c;
      const b=JSON.parse(raw), reviewer=b.messages[0].content.includes("OpenWorker's action reviewer");
      if(reviewer) {
        assert.match(b.messages[0].content,/Hard policy checks precede review/);
        reviews.push(JSON.parse(b.messages.find(m=>m.role==="user").content));
      } else {
        working.push(b);
        assert.ok(!JSON.stringify(b).match(/AGENT_REVIEW|SESSION_REVIEW|TURN_REVIEW|SUMMARY_GUIDANCE/),"Guidance never enters working-model messages");
      }
      const tool=!reviewer && b.messages.at(-1).role==="user";
      const delta=tool?{tool_calls:[{index:0,id:`c${working.length}`,type:"function",function:{name:"app__probe",arguments:"{}"}}]}:{content:reviewer?JSON.stringify({verdict:"allow",reason:"A synthetic read-only test."}):"Done."};
      res.writeHead(200,{"Content-Type":"text/event-stream"});
      res.write(`data: ${JSON.stringify({id:`r${reviews.length}-${working.length}`,choices:[{index:0,delta,finish_reason:tool?"tool_calls":"stop"}]})}\n\n`);
      res.write(`data: ${JSON.stringify({choices:[],usage:{prompt_tokens:60,completion_tokens:20}})}\n\n`);
      res.end("data: [DONE]\n\n");
    }catch(e){fixtureError=e;res.writeHead(500);res.end();}
  });
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  let h;
  t.after(async()=>{await h?.shutdown();server.closeAllConnections();await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});});
  async function start(){
    h=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:"off",compaction:{triggerAtContextFraction:0.7}});
    await h.setModelProvider({name:"Fixture",api:"chat-completions",baseUrl:`http://127.0.0.1:${server.address().port}/v1`});
    await h.registerTools({namespace:"app",tools:{probe:{description:"Read a synthetic value",inputSchema:{type:"object",properties:{},additionalProperties:false},execute:()=>{effects++;return {ok:true};}}}});
    const agent=h.createAgent({name:"guided",instructions:"Read the synthetic value.",tools:["app.probe"],builtinTools:{askUser:false,readHistory:false},defaultModel:{provider:"Fixture",name:"working"},approvals:{mode:"auto",reviewerModel:{provider:"Fixture",name:"reviewer"}},autoApproverContext:"AGENT_REVIEW",compaction:{additionalInstructions:"AGENT_SUMMARY_GUIDANCE"}});
    await agent.ready();return agent;
  }
  const agent=await start();
  const inherited=await agent.newSession();
  assert.equal((await inherited.snapshot()).session.auto_approver_context,"AGENT_REVIEW");
  const before=await h.request("session/list",{});
  for(const invalid of [{autoApproverContext:17},{autoApproverContext:"secret\0"},{autoApproverContext:"x".repeat(32001)},{compaction:{additionalInstructions:"x".repeat(32001)}}]){
    await assert.rejects(()=>agent.newSession(invalid));
    assert.deepEqual(await h.request("session/list",{}),before);
  }
  const session=await agent.newSession({autoApproverContext:"SESSION_REVIEW",compaction:{additionalInstructions:"SESSION_SUMMARY_GUIDANCE"}});
  const snap=await session.snapshot();
  assert.equal(snap.session.compaction_config.additional_instructions,"SESSION_SUMMARY_GUIDANCE");
  assert.equal(snap.session.compaction_config.threshold_pct,0.7,"Guidance preserves compaction tuning");
  const turn={submissionId:"guided",autoApproverContext:"TURN_REVIEW",compaction:{additionalInstructions:"TURN_SUMMARY_GUIDANCE"}};
  assert.equal((await (await session.send("Read the value.",turn)).result()).status,"completed");
  assert.equal(fixtureError,undefined);assert.equal(reviews.length,1);assert.equal(effects,1);
  assert.deepEqual(reviews[0].application_context,{source:"application",human_authorization:false,guidance:"TURN_REVIEW"});
  assert.ok(!JSON.stringify(reviews[0].user_inputs).includes("TURN_REVIEW"));
  assert.equal((await (await session.send("Read the value.",turn)).result()).status,"completed");
  assert.equal(effects,1,"Duplicate submission must not replay tools");
  for(const changes of [{autoApproverContext:"changed"},{compaction:{additionalInstructions:"changed"}}])await assert.rejects(()=>session.send("Read the value.",{...turn,...changes}));
  const ref=session.reference();await h.shutdown();h=undefined;
  await start();const resumed=await h.resume(ref);
  assert.equal((await resumed.snapshot()).turn.auto_approver_context,"TURN_REVIEW");
  assert.equal((await resumed.snapshot()).turn.compaction.additional_instructions,"TURN_SUMMARY_GUIDANCE");
  assert.equal((await resumed.run("Read it again.",{autoApproverContext:"",compaction:{additionalInstructions:""}})).status,"completed");
  assert.equal(reviews.at(-1).application_context.guidance,"");
  assert.equal((await resumed.run("Read it once more.")).status,"completed");
  assert.equal(reviews.at(-1).application_context.guidance,"SESSION_REVIEW");
  const final=await resumed.snapshot();
  assert.equal(final.session.auto_approver_context,"SESSION_REVIEW");
  assert.equal(final.session.compaction_config.additional_instructions,"SESSION_SUMMARY_GUIDANCE");
  assert.equal(final.turn.auto_approver_context,undefined);
  assert.equal(fixtureError,undefined);
});
