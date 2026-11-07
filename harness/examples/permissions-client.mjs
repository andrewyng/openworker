// Development acceptance: real Linux OpenShell + MCP, scripted or live model.
// No synthetic reviewer approvals: scripted reviewer is deliberately unsure.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';

const root=resolve('test-output','permissions-'+randomUUID());
await mkdir(root,{recursive:true});
const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const runner=process.env.OPENWORKER_RUNNER||binary;
const live=process.argv.includes('--live');
const sandbox=process.env.OPENWORKER_SANDBOX||'openshell';
if(process.argv.includes('--credential-stdin')){
  let raw='';for await(const part of process.stdin){raw+=part;if(raw.length>16384)throw Error('Credential too large');}
  const credential=JSON.parse(raw);if(typeof credential.api_key!=='string'||!credential.api_key)throw Error('Missing key');
  process.env.OPENAI_API_KEY=credential.api_key;raw='';credential.api_key='';
}
let seq=0;
class Client {
  pending=new Map();
  async start(){
    this.child=spawn(binary,['serve','--stdio','--sandbox',sandbox,'--state-dir',join(root,'state'),'--bundle',resolve('examples/assistant'),'--runner',runner,...(live?[]:['--scripted'])],{stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(resolve=>this.child.once('exit',resolve));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{
    const id=++seq;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Timeout: '+method));},25000);
    this.pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r);},reject:e=>{clearTimeout(timer);reject(e);}});
    this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });}
  async stop(signal){if(!this.child||this.child.exitCode!==null||this.child.signalCode)return;if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
let client=new Client();
const reports=[];
try{
  const init=await client.start();assert.deepEqual(init.capabilities.approval_modes,['ask','auto-approve','bypass']);
  for(const mode of live?['auto-approve']:['bypass','auto-approve','ask']){
    const workspace=join(root,mode);await mkdir(workspace);
    const permissions={mode,auto_skip_questions:true};
    const {session}=await client.request('session/create',{name:'Permission acceptance',coworker:'assistant-example',workspace,permissions});
    const sid=session.id;
    await client.request('turn/start',{session_id:sid,submission_id:'policy-1',text:'Isolated integration test. First call ask_user to ask which greeting to use. If the question is skipped, use Hello ExampleApp. Then call example_echo with Hello ExampleApp, write exactly Hello ExampleApp to relative proof.txt using write_file, and verify with run_shell using exactly cat proof.txt. Only these calls and this file are in scope. Do not deploy, access credentials, or inspect anything else. Finish with a brief factual result.'});
    let restarted=false,manual=0,completed=false;
    const until=Date.now()+480000;
    while(Date.now()<until){
      const snap=await client.request('session/attach',{session_id:sid});
      assert.deepEqual(snap.session.permissions,permissions);
      if(snap.turn.status==='completed'){completed=true;break;}
      if(['failed','interrupted','cancelled','truncated'].includes(snap.turn.status))throw Error('Turn failed: '+JSON.stringify(await client.request('event/read',{session_id:sid,after:0})));
      for(const i of snap.interactions){
        assert.equal(i.kind,'approval','Question skipping must not leave a question wait');
        assert.notEqual(mode,'bypass','Bypass must never ask for approval');
        if(!restarted){
          await client.stop('SIGKILL');client=new Client();await client.start();
          const recovered=await client.request('session/attach',{session_id:sid});
          assert.equal(recovered.interactions[0].id,i.id);assert.deepEqual(recovered.session.permissions,permissions);
          restarted=true;
        }
        const a=JSON.parse(i.call.arguments);
        const allowed=(i.call.name==='example_echo'&&a.text==='Hello ExampleApp')
          ||(i.call.name==='write_file'&&a.path==='proof.txt'&&typeof a.content==='string'&&a.content.length<4096)
          ||(i.call.name==='read_file'&&a.path==='proof.txt')
          ||(i.call.name==='run_shell'&&['cat proof.txt','pwd; cat proof.txt'].includes(a.command));
        assert.ok(allowed,'Test driver will not approve an unexpected action');
        try{await client.request('interaction/respond',{session_id:sid,interaction_id:i.id,decision:'once'});manual++;}
        catch(e){if(!e.message.includes('checkpointing'))throw e;}
      }
      await new Promise(r=>setTimeout(r,150));
    }
    assert.ok(completed,'Turn did not complete');
    assert.ok((await readFile(join(workspace,'proof.txt'),'utf8')).includes('Hello ExampleApp'));
    let cursor=0,events=[],more=true;
    while(more){const page=await client.request('event/read',{session_id:sid,after:cursor});events.push(...page.events);cursor=page.cursor;more=page.has_more;}
    assert.ok(events.some(e=>e.type==='interaction_resolved'&&e.data.origin==='question_policy'));
    assert.ok(events.some(e=>e.type==='tool_finished'&&e.data.name==='ask_user'&&e.data.result.status==='skipped'&&e.data.result.answer===null));
    const started=events.filter(e=>e.type==='tool_started');assert.ok(started.length>=3);
    assert.equal(new Set(started.map(e=>e.data.interaction_id)).size,started.length);
    if(mode==='bypass'){
      assert.equal(manual,0);assert.ok(started.every(e=>e.data.approval_origin==='bypass'));
      assert.ok(!events.some(e=>e.type==='review_started'||e.type==='interaction_required'));
    }else if(mode==='auto-approve'){
      assert.ok(events.some(e=>e.type==='review_started'));
      if(live)assert.ok(events.some(e=>e.data.review?.verdict==='allow'),'Live reviewer never allowed an action');
      else{assert.ok(restarted);assert.ok(manual>=3);assert.ok(events.some(e=>e.data.review?.verdict==='unsure'));}
    }else{assert.ok(restarted);assert.ok(manual>=3);assert.ok(!events.some(e=>e.type==='review_started'));}
    await writeFile(join(root,mode+'-events.json'),JSON.stringify(events,null,2),{mode:0o600});
    await client.request('session/close',{session_id:sid});
    reports.push({mode,manual,restarted,tool_calls:started.length,reviewer_allows:events.filter(e=>e.data.review?.verdict==='allow').length});
  }
  console.log(JSON.stringify({result:'PASS',model:live?'live':'scripted-test',sandbox,state:root,reports}));
}finally{await client.stop().catch(()=>{});}
