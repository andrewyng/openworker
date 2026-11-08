// Actual managed Rust host + synthetic loopback provider. No live model keys.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 120000 };
const initialHistory = [
  {type:"user",text:"Find a short name."},
  {type:"assistant",text:"Checking Atlas."},
  {type:"tool_call",callId:"old-lookup",name:"app__lookup",arguments:{name:"Atlas"}},
  {type:"tool_result",callId:"old-lookup",output:'{"available":false}'},
  {type:"assistant",text:"Atlas was taken."},
];

test("session context, import, built-ins and call limits survive the managed boundary", options, async t => {
  const root = await mkdtemp(join(tmpdir(),"ow-session-options-"));
  const requests = [];
  let effects=0, reply="normal", fixtureError;
  const server=createServer(async(req,res)=>{
    try {
      let raw="";
      for await(const chunk of req){raw+=chunk;assert.ok(raw.length<2_000_000);}
      const body=JSON.parse(raw);requests.push(body);
      assert.deepEqual(body.tools.map(t=>t.function.name),["app__lookup"]);
      if(requests.length===1){res.writeHead(503);res.end('{"error":{"message":"fixture transient"}}');return;}
      const tool=reply!=="normal" ? reply : requests.length===2 ? "app__lookup" : null;
      res.writeHead(200,{"Content-Type":"text/event-stream"});
      const chunk=delta=>res.write(`data: ${JSON.stringify({id:`r-${requests.length}`,choices:[{index:0,delta,finish_reason:tool?"tool_calls":"stop"}]})}\n\n`);
      if(tool)chunk({tool_calls:[{index:0,id:`live-${requests.length}`,type:"function",function:{name:tool,arguments:'{"name":"Lyra"}'}}]});
      else chunk({content:"Lyra is available."});
      res.write(`data: ${JSON.stringify({id:`r-${requests.length}`,choices:[],usage:{prompt_tokens:50,completion_tokens:5}})}\n\n`);
      res.end("data: [DONE]\n\n");
    }catch(error){fixtureError=error;res.writeHead(500);res.end();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  let harness;
  t.after(async()=>{await harness?.shutdown();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});});
  const definition={name:"names",instructions:"Help the user name things.",tools:["app.lookup"],builtinTools:{askUser:false,readHistory:false},approvals:{mode:"never"},limits:{maxModelCalls:8,maxOutputTokens:100000,maxOutputTokensPerCall:24000,summaryTokens:512},defaultModel:{provider:"Fixture",name:"fixture"}};
  async function start(){
    harness=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:"off"});
    await harness.setModelProvider({name:"Fixture",api:"chat-completions",baseUrl:`http://127.0.0.1:${server.address().port}/v1`});
    await harness.registerTools({namespace:"app",tools:{lookup:{description:"Check a name.",inputSchema:{type:"object",properties:{name:{type:"string"}},required:["name"],additionalProperties:false},execute:async()=>{effects++;return {available:true};}}}});
    const agent=harness.createAgent(definition);await agent.ready();return agent;
  }
  let agent=await start();
  for(const history of [
    [{type:"user",text:"hi",_runtime_notice:"team_delivery"}],
    [{type:"assistant",text:"missing user"}],
    [{type:"user",text:"hi"},{type:"tool_call",callId:"unfinished",name:"app__lookup",arguments:{}}],
    [{type:"user",text:"hi"},{type:"tool_result",callId:"orphan",output:"done"}],
  ]) {
    const before=await harness.request("session/list",{});
    await assert.rejects(()=>agent.newSession({initialHistory:history}));
    assert.deepEqual(await harness.request("session/list",{}),before,"Invalid import must not leave an empty session");
  }
  const session=await agent.newSession({initialHistory});
  assert.equal(effects,0,"Import never executes historical tools");
  const sendOptions={submissionId:"preferences-a",instructionAddendum:"Prefer .ai domains; budget is $40."};
  const run=await session.send("Suggest another name.",sendOptions);
  assert.equal((await run.result()).status,"completed");
  assert.equal(fixtureError,undefined);
  assert.equal(requests.length,3,"One transient retry and one tool follow-up");
  assert.equal(effects,1);
  for(const body of requests){
    assert.match(body.messages[0].content,/Prefer \.ai domains; budget is \$40\./);
    assert.equal(body.max_tokens,24000);
    assert.ok(body.messages.some(m=>m.role==="tool"&&m.tool_call_id==="old-lookup"));
    assert.ok(body.messages.some(m=>m.role==="user"&&m.content==="Suggest another name."));
    assert.ok(!body.messages.filter(m=>m.role==="user").some(m=>m.content.includes("Prefer .ai")));
  }
  assert.equal((await (await session.send("Suggest another name.",sendOptions)).result()).status,"completed");
  assert.equal(requests.length,3,"Identical submission must not rerun");
  await assert.rejects(()=>session.send("Suggest another name.",{...sendOptions,instructionAddendum:"Changed preference"}));
  await assert.rejects(()=>session.send("Suggest another name.",{...sendOptions,output:{bufferAttempts:true}}));
  const second=await session.send("Now use a different budget.",{limits:{maxModelCalls:2,maxOutputTokens:2048,maxOutputTokensPerCall:128,summaryTokens:512}});
  assert.equal((await second.result()).status,"completed");
  assert.equal(requests.at(-1).max_tokens,128);
  assert.ok(!requests.at(-1).messages[0].content.includes("Prefer .ai"));
  const ref=session.reference();
  await harness.shutdown();harness=undefined;
  agent=await start();
  const resumed=await harness.resume(ref);
  assert.equal((await (await resumed.send("Continue after restart.")).result()).status,"completed");
  assert.ok(!requests.at(-1).messages[0].content.includes("Prefer .ai"));
  assert.ok(requests.at(-1).messages.some(m=>m.role==="tool"&&m.tool_call_id==="old-lookup"));
  assert.equal(effects,1);
  for(const unexpected of ["ask_user","read_history"]){
    reply=unexpected;
    const rejected=await agent.newSession();
    assert.equal((await (await rejected.send("Respond.")).result()).status,"failed");
    assert.equal(effects,1);
  }
  assert.equal(fixtureError,undefined);
});
