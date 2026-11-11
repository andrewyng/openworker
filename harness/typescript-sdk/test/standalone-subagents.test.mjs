// Real managed Rust host and HTTP provider: no cloud key or subprocess mocks.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Harness} from '../dist/index.js';
const frame = response => `data: ${JSON.stringify({type:'response.completed',response:{status:'completed',output:response,usage:{input_tokens:12,output_tokens:4}}})}\n\n`;
const message = text => ({type:'message',role:'assistant',content:[{type:'output_text',text}]});
for(const [execution,unexpected] of [['sync',false],['async',false],['sync',true]]) {
  test(`standalone ${execution} unexpected=${unexpected}: fresh context, tools and lineage`,{skip:!process.env.OW_TS_TEST_HOST,timeout:90000},async t=>{
    const root=await mkdtemp(join(tmpdir(),'ow-subagent-ts-'));
    let h, fault, parentCalls=0, childCalls=0, childFinished=false, release;
    const gate=new Promise(resolve=>{release=resolve;});
    const server=createServer(async(req,res)=>{
      try {
        let text='';for await(const chunk of req)text+=chunk;
        const body=JSON.parse(text);
        const names=(body.tools??[]).map(v=>v.name);
        const child=body.instructions.includes('CHILD_ROLE');
        res.writeHead(200,{'Content-Type':'text/event-stream'});
        if(child){
          childCalls++;assert.equal(childCalls,1);
          assert.deepEqual(names,['send_message']);
          assert.ok(JSON.stringify(body).includes('CHILD_BRIEFING'));
          assert.ok(!JSON.stringify(body).includes('PARENT_PRIVATE_SENTINEL'));
          assert.ok(!body.instructions.includes('PARENT_ROLE'));
          if(execution==='async')await gate;
          childFinished=true;res.end(frame(unexpected ? [{type:'function_call',call_id:'escape',name:'coding__write_file',arguments:JSON.stringify({path:'forbidden.txt',content:'must not write'})}] : [message('Child evidence ready.')]));
        }else{
          parentCalls++;assert.ok(names.includes('create_sub_agent'));
          assert.ok(!names.includes('team_post_board'));
          if(parentCalls===1){
            res.end(frame([{type:'function_call',call_id:'create-reader',name:'create_sub_agent',arguments:JSON.stringify({agent_id:'reader',system_prompt:'CHILD_ROLE: inspect only the briefing.',context:'CHILD_BRIEFING',tools:[],execution})}]));
          }else res.end(frame([message('Parent finished.')]));
        }
      }catch(e){fault=e;console.error("Fixture assertion:",e.message);res.end();}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    t.after(async()=>{release();await h?.shutdown();server.closeAllConnections();await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});});
    h=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:root,sandbox:'off',agents:[{id:'startup',instructions:'Answer briefly.',tools:[]}]});
    await h.setModelProvider({name:'Fixture',api:'responses',baseUrl:`http://127.0.0.1:${server.address().port}/v1`,credentials:{apiKey:'synthetic-fixture-key',storage:'memory'}});
    const a=h.createAgent({name:'parent',instructions:'PARENT_ROLE',tools:[],subagents:{enabled:true},approvals:{mode:'never'},defaultModel:{provider:'Fixture',name:'fixture'}});
    await a.ready();const s=await a.newSession();
    const run=await s.send('PARENT_PRIVATE_SENTINEL: delegate the explicit briefing.');
    const result=await run.result();assert.equal(result.status,'completed',JSON.stringify(result));
    if(execution==='async'){assert.equal(childFinished,false,'async must not wait for the child');release();}
    else {
      const events=[];for await(const e of run.events())events.push(e);
      assert.ok(events.some(e=>e.agentId==='reader' && e.parentSessionId===s.id),'child event lineage');
      if(unexpected){assert.ok(events.some(e=>e.agentId==='reader' && e.event.type==='turn_completed' && e.event.data.status==='failed'));assert.ok(!events.some(e=>e.agentId==='reader' && e.event.type==='tool_started'));}
    }
    for(let i=0;i<600&&!childFinished&&!fault;i++)await new Promise(r=>setTimeout(r,50));
    assert.equal(childFinished,true);assert.equal(childCalls,1);assert.equal(fault,undefined);
  });
}
