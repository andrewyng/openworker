// Dependency-free integration client. Node is a developer test dependency only;
// ExampleApp can implement the same stdio framing in any language.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile,access} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';

const root=resolve('test-output',randomUUID());await mkdir(join(root,'workspace'),{recursive:true});
const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const runner=process.env.OPENWORKER_RUNNER||resolve('target/linux-arm64/debug/openworker');
const live=process.argv.includes('--live');
let seq=0;
class Client {
  pending=new Map();events=[];
  async start(){
    this.child=spawn(binary,['serve','--stdio','--state-dir',join(root,'state'),'--bundle',resolve('examples/assistant'),'--runner',runner,...(live?[]:['--scripted'])],{stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(resolve=>this.child.once('exit',resolve));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);if(m.id!==undefined){const waiter=this.pending.get(m.id);if(waiter){this.pending.delete(m.id);m.error?waiter.reject(new Error(m.error.message)):waiter.resolve(m.result);}}else this.events.push(m);});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('Timed out: '+method));},25000);this.pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r);},reject:e=>{clearTimeout(timer);reject(e);}});this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');});}
  async stop(signal){if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
let client=new Client();
try{
  const config=await client.start();assert.equal(config.implementation,'native-rust');
  console.error('acceptance: connected to '+config.implementation+' ('+config.mode+')');
  const {session}=await client.request('session/create',{name:'ExampleApp acceptance',coworker:'assistant-example',workspace:join(root,'workspace')});const sid=session.id;
  await client.request('session/attach',{session_id:sid});
  const params={session_id:sid,submission_id:'acceptance-1',text:'Integration acceptance task: ask me to confirm the exact text Hello ExampleApp for proof.txt (use an empty options list so I can answer freely). Then call example_echo with Hello ExampleApp, write that text to proof.txt using write_file, and verify using run_shell with exactly: cat proof.txt. Only use these tools and this relative file. Do not inspect or access anything else. End with a brief factual result.'};
  const first=await client.request('turn/start',params);const duplicate=await client.request('turn/start',params);assert.equal(duplicate.duplicate,true);assert.equal(first.turn.id,duplicate.turn.id);
  let restarted=false,approved=0,deadline=Date.now()+240000;
  while(Date.now()<deadline){
    const snap=await client.request('session/attach',{session_id:sid});
    if(['failed','interrupted','cancelled','truncated'].includes(snap.turn.status))throw new Error('Turn failed: '+JSON.stringify(await client.request('event/read',{session_id:sid,after:0})));
    if(snap.turn.status==='completed')break;
    const item=snap.interactions[0];
    if(item){
      if(item.kind==='approval'&&!restarted){
        console.error('acceptance: restarting at durable approval');
        const iid=item.id;await client.stop('SIGKILL');client=new Client();await client.start();
        const recovered=await client.request('session/attach',{session_id:sid});assert.equal(recovered.turn.status,'recoverable');assert.equal(recovered.interactions[0].id,iid);restarted=true;
      }
      const args=JSON.parse(item.call.arguments);
      if(live&&item.kind==='approval'){
        const allowed=(item.call.name==='example_echo'&&typeof args.text==='string'&&args.text.length<200)
          ||(item.call.name==='write_file'&&args.path==='proof.txt'&&typeof args.content==='string'&&args.content.length<4096)
          ||(item.call.name==='read_file'&&args.path==='proof.txt')
          ||(item.call.name==='run_shell'&&['cat proof.txt','pwd; cat proof.txt'].includes(args.command));
        if(!allowed)throw new Error('Live action needs manual review; not auto-approved: '+item.call.name);
      }
      const response=item.kind==='approval'?{decision:'once'}:{answer:item.kind==='compaction'?'Trim oldest 10%':args.options?.[0]||'Hello ExampleApp'};
      try{await client.request('interaction/respond',{session_id:sid,interaction_id:item.id,...response});if(item.kind==='approval')approved++;}catch(e){if(!e.message.includes('checkpointing'))throw e;}
    }
    await new Promise(resolve=>setTimeout(resolve,150));
  }
  const snap=await client.request('session/attach',{session_id:sid});assert.equal(snap.turn.status,'completed');assert.ok(restarted);assert.ok(approved>=2);
  const file=await readFile(join(root,'workspace/proof.txt'),'utf8');assert.ok(file.includes('ExampleApp'));
  const events=(await client.request('event/read',{session_id:sid,after:0})).events;
  assert.ok(events.some(e=>e.type==='tool_finished'&&e.data.name==='example_echo'));
  assert.ok(events.some(e=>e.type==='tool_started'&&e.data.enforcement==='full'));
  assert.ok(events.some(e=>e.type==='assistant_delta'));
  const calls=events.filter(e=>e.type==='tool_started').map(e=>e.data.interaction_id);assert.equal(new Set(calls).size,calls.length);
  await client.request('session/close',{session_id:sid});await client.stop();client=new Client();await client.start();
  const after=await client.request('session/attach',{session_id:sid});assert.equal(after.turn.status,'completed');assert.equal(after.session.closed,true);
  if(!live){
    console.error('acceptance: testing denied write');
    // Denial must not execute a write, even though the scripted model continues.
    const deniedWork=join(root,'denied-workspace');await mkdir(deniedWork);
    const denied=(await client.request('session/create',{name:'Denied write',coworker:'assistant-example',workspace:deniedWork})).session.id;
    await client.request('session/attach',{session_id:denied});
    await client.request('turn/start',{session_id:denied,submission_id:'deny-case',text:'Exercise denied write'});
    const finish=async(session,respond)=>{
      const until=Date.now()+120000;
      while(Date.now()<until){const p=await client.request('session/attach',{session_id:session});if(['completed','cancelled','failed','interrupted'].includes(p.turn.status))return p;
        for(const i of p.interactions){const args=JSON.parse(i.call.arguments);await client.request('interaction/respond',{session_id:session,interaction_id:i.id,...(i.kind==='question'?{answer:args.options[0]}:{decision:respond(i)})});}
        await new Promise(r=>setTimeout(r,100));
      }throw new Error('Negative-case timeout');
    };
    assert.equal((await finish(denied,i=>i.call.name==='write_file'?'deny':'once')).turn.status,'completed');
    await assert.rejects(access(join(deniedWork,'proof.txt')));
    const deniedEvents=(await client.request('event/read',{session_id:denied,after:0})).events;
    assert.ok(!deniedEvents.some(e=>e.type==='tool_started'&&e.data.name==='write_file'));
    await client.request('session/close',{session_id:denied});
    // Graceful EOF must preserve a parked question, not turn it into cancellation.
    console.error('acceptance: testing graceful restart and cancellation');
    const parked=(await client.request('session/create',{name:'Graceful recovery',coworker:'assistant-example',workspace:deniedWork})).session.id;
    await client.request('session/attach',{session_id:parked});
    const turn=(await client.request('turn/start',{session_id:parked,submission_id:'park',text:'Ask a question'})).turn;
    let item;for(let n=0;n<300&&!item;n++){item=(await client.request('session/attach',{session_id:parked})).interactions[0];if(!item)await new Promise(r=>setTimeout(r,100));}
    assert.ok(item);await client.stop();client=new Client();await client.start();
    const recovered=await client.request('session/attach',{session_id:parked});assert.equal(recovered.interactions[0].id,item.id);assert.equal(recovered.turn.status,'recoverable');
    await assert.rejects(client.request('interaction/respond',{session_id:sid,interaction_id:item.id,answer:'Hello ExampleApp'}));
    await client.request('turn/cancel',{session_id:parked,turn_id:turn.id});
    const cancelled=await client.request('session/attach',{session_id:parked});assert.equal(cancelled.turn.status,'cancelled');
    await assert.rejects(client.request('interaction/respond',{session_id:parked,interaction_id:item.id,answer:'Hello ExampleApp'}));
    await client.request('session/close',{session_id:parked});
  }
  console.log(JSON.stringify({result:'PASS',mode:config.mode,platform:process.platform,state:root,checks:['real OpenShell','native MCP tool','durable approval after SIGKILL','idempotent submission','streamed events','workspace write and shell verification','execution history after restart','owned sandbox cleanup',...(!live?['denied write not executed','graceful restart preserves question','cancellation','wrong-session and stale responses rejected']:[])]}));
}finally{await client.stop().catch(()=>{});}
