// Actual managed Rust host, synthetic loopback streams, no provider credentials.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness, AttemptOutput, guard } from "../dist/index.js";

const options={skip:!process.env.OW_TS_TEST_HOST,timeout:90000};
const frame=value=>`data: ${JSON.stringify(value)}\n\n`;
function stream(api, text, reasoning, complete=true) {
  if(api==="responses") return frame({type:"response.reasoning_summary_text.delta",delta:reasoning,output_index:0,summary_index:0})+
    frame({type:"response.output_text.delta",delta:text})+
    (complete?frame({type:"response.completed",response:{status:"completed",output:[{type:"reasoning",encrypted_content:"PRIVATE_SIGNATURE"},{type:"message",role:"assistant",content:[{type:"output_text",text}]}],usage:{input_tokens:10,output_tokens:8}}}):"");
  if(api==="chat-completions") {
    const chunk=(delta,finish_reason=null)=>frame({id:"fixture",choices:[{index:0,delta,finish_reason}]});
    return chunk({reasoning_content:reasoning})+chunk({content:text})+(complete?chunk({},"stop")+"data: [DONE]\n\n":"");
  }
  const events=[
    {type:"message_start",message:{id:"fixture",role:"assistant",content:[],usage:{input_tokens:10,output_tokens:0}}},
    {type:"content_block_start",index:0,content_block:{type:"thinking",thinking:""}},
    {type:"content_block_delta",index:0,delta:{type:"thinking_delta",thinking:reasoning}},
    {type:"content_block_delta",index:0,delta:{type:"signature_delta",signature:"PRIVATE_SIGNATURE"}},
    {type:"content_block_stop",index:0},
    {type:"content_block_start",index:1,content_block:{type:"text",text:""}},
    {type:"content_block_delta",index:1,delta:{type:"text_delta",text}},
  ];
  if(complete)events.push({type:"content_block_stop",index:1},{type:"message_delta",delta:{stop_reason:"end_turn"},usage:{output_tokens:8}},{type:"message_stop"});
  return events.map(frame).join("");
}
async function setup(t,api,mode="normal") {
  const root=await mkdtemp(join(tmpdir(),"ow-stream-events-"));
  const requests=[];let serverError;
  const server=createServer(async(req,res)=>{
    try{
      let body="";for await(const part of req){body+=part;assert.ok(body.length<1_000_000);}
      const input=JSON.parse(body);requests.push(input);
      assert.deepEqual(input.tools??[],[]);
      if(mode==="error") {res.writeHead(401,{"x-request-id":"req_fixture"});res.end(JSON.stringify({error:{code:"invalid_api_key",type:"authentication_error",message:"SECRET_UPSTREAM_BODY"}}));return;}
      const failed=mode==="retry"&&requests.length===1;
      res.writeHead(200,{"content-type":"text/event-stream"});
      res.end(stream(api,failed?"FAILED_TEXT":"Accepted answer",failed?"FAILED_REASON":"Display reasoning",!failed));
    }catch(error){serverError=error;res.destroy();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  let h;t.after(async()=>{await h?.shutdown();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});assert.equal(serverError,undefined);});
  h=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:"off"});
  await h.setModelProvider({name:"Fixture",api,baseUrl:`http://127.0.0.1:${server.address().port}/v1`,credentials:{apiKey:"SYNTHETIC_KEY_SENTINEL",storage:"memory"},...(api==="anthropic"?{thinking:{type:"adaptive"}}:{})});
  const agent=h.createAgent({name:"guide",instructions:"Give a hint.",tools:[],memory:false,planning:false,scheduling:false,defaultModel:{provider:"Fixture",name:"fixture"}});
  return {h,agent,requests};
}
for(const api of ["responses","chat-completions","anthropic"]){
  test(`reasoning is separate, opt-in and suppressed by output guardrails (${api})`,options,async t=>{
    const {agent,requests}=await setup(t,api);
    for(const [enabled,protectedOutput] of [[false,false],[true,false],[true,true]]){
      const session=await agent.newSession();
      const run=await session.send("Explain",{output:{reasoning:enabled},...(protectedOutput?{guardrails:{modelOutput:[guard.regex({name:"protect",pattern:"forbidden"})]}}:{})});
      const events=[];for await(const item of run.events())events.push(item);
      assert.equal((await run.result()).status,"completed");
      const reasoning=events.filter(e=>e.summary?.type==="reasoning_delta");
      assert.equal(reasoning.length,enabled&&!protectedOutput?1:0);
      if(reasoning.length){assert.equal(reasoning[0].summary.text,"Display reasoning");assert.ok(reasoning[0].summary.attemptId);}
      assert.ok(!JSON.stringify(events).includes("PRIVATE_SIGNATURE"));
      if(!enabled||protectedOutput)assert.ok(!JSON.stringify(events).includes("Display reasoning"));
      const body=requests.at(-1);
      if(api==="responses")assert.deepEqual(body.reasoning,enabled&&!protectedOutput?{summary:"auto"}:undefined);
      if(api==="anthropic")assert.deepEqual(body.thinking,{type:"adaptive"});
    }
  });
  for(const buffered of [false,true])test(`failed attempts are replaceable and optionally buffered (${api}, ${buffered})`,options,async t=>{
    const {agent,requests}=await setup(t,api,"retry");
    const run=await (await agent.newSession()).send("Explain",{output:{reasoning:true,bufferAttempts:buffered}});
    const projection=new AttemptOutput();const events=[];
    for await(const item of run.events()){projection.apply(item);events.push(item);}
    assert.equal((await run.result()).status,"completed");assert.equal(requests.length,2);
    assert.equal(projection.text,"Accepted answer");assert.equal(projection.reasoning,"Display reasoning");
    assert.equal(events.filter(e=>e.summary?.type==="model_attempt_started").length,2);
    assert.deepEqual(events.filter(e=>e.summary?.type==="model_attempt_finished").map(e=>e.summary.status),["failed","completed"]);
    assert.equal(JSON.stringify(events).includes("FAILED_TEXT"),!buffered);
    assert.equal(JSON.stringify(events).includes("FAILED_REASON"),!buffered);
  });
  test(`safe provider diagnostics survive result and probe errors (${api})`,options,async t=>{
    const {h,agent,requests}=await setup(t,api,"error");
    const run=await (await agent.newSession()).send("Explain");
    const events=[];for await(const item of run.events())events.push(item);
    const result=await run.result();assert.equal(result.status,"failed");
    assert.deepEqual(result.providerError,{category:"authentication",retryable:false,httpStatus:401,requestId:"req_fixture",api,code:"invalid_api_key"});
    const error=events.find(e=>e.summary?.type==="provider_error");assert.deepEqual(error.summary.diagnostic,result.providerError);
    await assert.rejects(h.testModelProvider({provider:"Fixture",name:"fixture"}),e=>e.code==="Remote"&&e.context?.providerError?.httpStatus===401);
    assert.equal(requests.length,2);
    assert.ok(!JSON.stringify([events,result]).includes("SECRET_UPSTREAM_BODY"));assert.ok(!JSON.stringify([events,result]).includes("SYNTHETIC_KEY_SENTINEL"));
  });
}
