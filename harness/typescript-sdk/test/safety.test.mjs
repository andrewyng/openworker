import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Harness,guard,hook} from "../dist/index.js";

test("guardrail factory preserves named blockedOutputOptions and rejects unsupported regex flags",()=>{
  assert.equal(guard.creditCard({action:"redact",keepLast:4}).action.keepLast,4);
  assert.throws(()=>guard.regex({name:"bad",pattern:/x/y}));
  assert.equal(guard.model({name:"pii",model:{provider:"Privacy",name:"fixture"},instructions:"Detect SSNs"}).detector.model.connection,"Privacy");
});

for(const protocol of ["responses","chat-completions","anthropic"]) {
test(`real child gates content, pauses, restores callbacks and scopes checks (${protocol})`,{skip:!process.env.OW_TS_TEST_HOST,timeout:120000},async t=>{
  const root=await mkdtemp(join(tmpdir(),"ow-safety-ts-"));
  let requests=0,callbackCalls=0,serverError;
  const server=createServer(async(req,res)=>{
    try {
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const input=JSON.parse(Buffer.concat(chunks).toString());
      assert.ok(!JSON.stringify(input).includes("SECRET_SENTINEL"));requests++;
      const frame=v=>`data: ${JSON.stringify(v)}\n\n`;
      res.writeHead(200,{"content-type":"text/event-stream"});
      if(protocol==="responses"){
        res.write(frame({type:"response.output_text.delta",delta:"SECRET_"}));res.write(frame({type:"response.output_text.delta",delta:"SENTINEL"}));
        res.end(frame({type:"response.completed",response:{status:"completed",output:[{type:"message",role:"assistant",content:[{type:"output_text",text:"SECRET_SENTINEL"}]}],usage:{input_tokens:10,output_tokens:8}}}));
      } else if(protocol==="chat-completions"){
        res.write(frame({id:"fixture",choices:[{index:0,delta:{content:"SECRET_"},finish_reason:null}]}));res.write(frame({id:"fixture",choices:[{index:0,delta:{content:"SENTINEL"},finish_reason:"stop"}]}));res.end("data: [DONE]\n\n");
      } else {
        for(const v of [
          {type:"message_start",message:{id:"fixture",role:"assistant",content:[],usage:{input_tokens:10,output_tokens:0}}},
          {type:"content_block_start",index:0,content_block:{type:"text",text:""}},
          {type:"content_block_delta",index:0,delta:{type:"text_delta",text:"SECRET_SENTINEL"}},
          {type:"content_block_stop",index:0},
          {type:"message_delta",delta:{stop_reason:"end_turn"},usage:{output_tokens:8}},{type:"message_stop"}
        ])res.write(frame(v));res.end();
      }
    }catch(e){serverError=e;res.destroy();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const privacy=guard.regex({name:"redact",pattern:/SECRET_[A-Z]+/g,action:async({text,matches})=>{callbackCalls++;assert.equal(matches[0].text,"SECRET_SENTINEL");return text.replace(/SECRET_[A-Z]+/g,"[hidden]");}});
  const observed=[];
  let harness;
  t.after(async()=>{await harness?.shutdown();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});});
  harness=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:join(root,"state"),sandbox:"off",compliance:{retentionDays:30},hooks:[hook.onEvent({name:"activity",run:c=>{observed.push(c.context);assert.ok(!JSON.stringify(c.context).includes("SECRET_SENTINEL"));}})],guardrails:{userInput:[privacy],modelOutput:[privacy]},restart:{maxRestarts:2,delayMs:20}});
  const provider={name:"Fixture",api:protocol,baseUrl:`http://127.0.0.1:${server.address().port}/v1`,credentials:{apiKey:"synthetic-fixture-key",storage:"memory"}};
  await harness.setModelProvider(provider);
  const agent=harness.createAgent({name:"guide",instructions:"Give a hint.",tools:[],memory:false,planning:false,scheduling:false,defaultModel:{provider:"Fixture",name:"fixture"}});
  const session=await agent.newSession();
  async function runSafe(s=session){
    const run=await s.send("SECRET_SENTINEL");const events=[];
    for await(const event of run.events()){events.push(event);assert.ok(!JSON.stringify(event).includes("SECRET_SENTINEL"));}
    const result=await run.result();assert.equal(result.status,"completed");assert.equal(result.finalOutput,"[hidden]");return events;
  }
  await runSafe();assert.equal(callbackCalls,2);
  const evidence=await session.complianceActivity();assert.ok(!JSON.stringify(evidence).includes("SECRET_SENTINEL"));assert.ok(evidence.activities.some(e=>e.kind==="safety_evaluation"));
  await assert.rejects(session.send("DENIED",{guardrails:{userInput:[guard.regex({name:"deny-turn",pattern:"DENIED"})]}}));assert.equal(requests,1);
  await runSafe();assert.equal(requests,2);

  let available=false;
  const unavailable=await agent.newSession({hooks:[hook.beforeOutput({name:"availability",run:()=>{if(!available)throw new Error("private scanner detail");return {action:"allow"};}})]});
  const run=await unavailable.send("hello");const result=await run.result();assert.equal(result.status,"interrupted");assert.equal(result.interruptions[0].kind,"safety");assert.ok(!JSON.stringify(result).includes("private scanner detail"));
  available=true;await unavailable.resumeSafety();assert.equal((await unavailable.resume()).status,"completed");

  let cancelled=false;
  const timeout=await agent.newSession({hooks:[hook.beforeModel({name:"timeout",timeoutMs:30,run:({signal})=>new Promise(()=>{signal.addEventListener("abort",()=>{cancelled=true;});})})]});
  const timed=await timeout.send("hello");assert.equal((await timed.result()).interruptions[0].kind,"safety");
  for(let i=0;i<20&&!cancelled;i++)await new Promise(r=>setTimeout(r,10));assert.equal(cancelled,true);
  await timeout.cancel();

  const pid=harness.pid;process.kill(pid,"SIGKILL");
  for(let i=0;i<100&&harness.pid===pid;i++)await new Promise(r=>setTimeout(r,20));
  await harness.ready();await harness.setModelProvider(provider);await runSafe();
  for(let i=0;i<100&&observed.length===0;i++)await new Promise(r=>setTimeout(r,20));assert.ok(observed.length>0);
  assert.equal(serverError,undefined);
});

}
