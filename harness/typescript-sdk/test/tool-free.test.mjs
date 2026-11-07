// Actual Rust child + private stdio + synthetic HTTP/SSE providers. No live keys.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";

const unexpected = ["app__unused", "ask_user", "read_history", "unregistered_tool"];
const frame = value => `data: ${JSON.stringify(value)}\n\n`;
function frames(api, tool) {
  if (api === "responses") {
    const prefix = tool ? "" : frame({type:"response.output_text.delta",delta:"Hello "});
    const tail = (tool ? "" : frame({type:"response.output_text.delta",delta:"learner."})) +
      frame({type:"response.completed",response:{status:"completed",output:tool
        ? [{type:"function_call",call_id:"unexpected",name:tool,arguments:"{}"}]
        : [{type:"message",role:"assistant",content:[{type:"output_text",text:"Hello learner."}]}],
        usage:{input_tokens:10,output_tokens:4}}});
    return [prefix,tail];
  }
  if (api === "chat-completions") {
    const chunk = (delta, finish_reason = null) => frame({id:"fixture",choices:[{index:0,delta,finish_reason}]});
    return [tool ? "" : chunk({content:"Hello "}),
      chunk(tool ? {tool_calls:[{index:0,id:"unexpected",type:"function",function:{name:tool,arguments:"{}"}}]} : {content:"learner."},tool ? "tool_calls" : "stop") + "data: [DONE]\n\n"];
  }
  const prefix = frame({type:"message_start",message:{id:"fixture",role:"assistant",content:[],usage:{input_tokens:10,output_tokens:0}}}) +
    frame({type:"content_block_start",index:0,content_block:tool
      ? {type:"tool_use",id:"unexpected",name:tool,input:{}} : {type:"text",text:""}}) +
    (tool ? "" : frame({type:"content_block_delta",index:0,delta:{type:"text_delta",text:"Hello "}}));
  const tail = (tool ? "" : frame({type:"content_block_delta",index:0,delta:{type:"text_delta",text:"learner."}})) +
    frame({type:"content_block_stop",index:0}) +
    frame({type:"message_delta",delta:{stop_reason:tool ? "tool_use" : "end_turn"},usage:{output_tokens:4}}) +
    frame({type:"message_stop"});
  return [prefix,tail];
}

for (const api of ["responses","chat-completions","anthropic"]) {
  test(`tool-free guide registers, streams and rejects every unselected tool (${api})`,
    {skip:!process.env.OW_TS_TEST_HOST,timeout:120000},async t => {
      const root = await mkdtemp(join(tmpdir(),"ow-tool-free-ts-"));
      let release;
      const gate = new Promise(resolve => {release=resolve;});
      let timer;
      const state = {requests:0,effects:0,finished:false,error:null};
      const server = createServer(async (req,res) => {
        try {
          let raw="";
          for await(const chunk of req){raw+=chunk;assert.ok(raw.length<1_000_000);}
          const body=JSON.parse(raw);
          assert.deepEqual(body.tools ?? [],[],"Provider must not receive implicit tools");
          assert.equal(body.tool_choice,undefined);
          assert.equal(body.stream,true);
          const index=state.requests++;
          assert.ok(index<=unexpected.length,"Unexpected retry/provider request");
          assert.equal(req.url,`/v1/${api === "responses" ? "responses" : api === "anthropic" ? "messages" : "chat/completions"}`);
          const [prefix,tail]=frames(api,index ? unexpected[index-1] : null);
          res.writeHead(200,{"Content-Type":"text/event-stream"});res.flushHeaders();
          res.write(prefix);
          // Bound streaming from the provider's first write, independently of
          // cold host/agent startup (notably slow bind mounts in Linux Docker).
          if(index===0){timer=setTimeout(release,15000);await gate;clearTimeout(timer);state.finished=true;}
          res.end(tail);
        } catch(error) {state.error=error;res.writeHead(500);res.end();}
      });
      await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
      let h;
      t.after(async()=>{release();clearTimeout(timer);await h?.shutdown();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});});
      h=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:"off",
        agents:[{id:"startup_guide",instructions:"Answer using supplied lesson context only.",tools:[],memory:false,planning:false,scheduling:false}]});
      await h.setModelProvider({name:"Fixture",api,baseUrl:`http://127.0.0.1:${server.address().port}/v1`,credentials:{apiKey:"synthetic-fixture-key",storage:"memory"}});
      await h.registerTools({namespace:"app",tools:{unused:{description:"Must not be inherited by guide.",inputSchema:{type:"object",properties:{},additionalProperties:false},
        execute:async()=>{state.effects++;return {ran:true};}}}});
      const definition={name:"guide",instructions:"Answer using supplied lesson context only.",tools:[],memory:false,planning:false,scheduling:false,defaultModel:{provider:"Fixture",name:"fixture"}};
      const guide=h.createAgent(definition);await guide.ready();
      await h.createAgent(definition).ready();
      assert.deepEqual(await h.listAgents(),["guide","startup_guide"]);
      const session=await guide.newSession();
      const run=await session.send("Give me a hint.");
      let text="",sawDelta=false;
      for await(const item of run.events()) {
        assert.ok(!["tool_started","tool_finished","interaction_required"].includes(item.event.type));
        if(item.event.type==="assistant_delta"){
          if(!sawDelta){assert.equal(state.finished,false);sawDelta=true;release();}
          text+=item.event.data.text;
        }
      }
      assert.equal(sawDelta,true,"Text must stream before provider completion");
      assert.equal(text,"Hello learner.");
      const result=await run.result();assert.equal(result.status,"completed");assert.equal(result.finalOutput,"Hello learner.");
      for(const name of unexpected){
        const s=await guide.newSession();const r=await s.send(`Unexpected tool ${name}.`);const result=await r.result();
        assert.equal(result.status,"failed",JSON.stringify(result));assert.deepEqual(result.interruptions,[]);
        for await(const item of r.events())assert.ok(!["tool_started","tool_finished","interaction_required"].includes(item.event.type),name);
      }
      assert.equal(state.error,null);assert.equal(state.requests,5);assert.equal(state.effects,0);
    });
}
