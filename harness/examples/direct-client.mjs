// Linux-only lifecycle acceptance, real native MCP, no model/network credentials.
// PATH intentionally lacks Docker/OpenShell. Uses only temporary synthetic work.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile,access,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const root=resolve('test-output','direct-'+randomUUID());await mkdir(root,{recursive:true});
let sequence=0;
class Client{
  pending=new Map();
  constructor(label,sandbox){this.state=join(root,label);this.sandbox=sandbox;}
  async start(){
    this.child=spawn(binary,['serve','--stdio','--scripted','--state-dir',this.state,'--bundle',resolve('examples/assistant'),...(this.sandbox?['--sandbox',this.sandbox]:[])],
      {env:{...process.env,PATH:'/deliberately-no-docker-or-openshell',OPENAI_API_KEY:'SYNTHETIC-TEST-KEY'},stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(r=>this.child.once('exit',r));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{
    const id=++sequence;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('RPC timeout: '+method));},25000);
    this.pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});
    this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });}
  async stop(signal){if(!this.child||this.child.exitCode!==null||this.child.signalCode)return;if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
async function until(read,predicate,label){const end=Date.now()+20000;while(Date.now()<end){const v=await read();if(predicate(v))return v;await new Promise(r=>setTimeout(r,40));}throw Error('Timed out: '+label);}
async function absent(pid){await until(async()=>{try{await access('/proc/'+pid);return false;}catch{return true;}},v=>v,'process exit');}
async function create(c,label,mode='bypass',skip=true){const workspace=join(root,label);await mkdir(workspace);return (await c.request('session/create',{name:label,coworker:'assistant-example',workspace,permissions:{mode,auto_skip_questions:skip}})).session.id;}
async function events(c,sid){let after=0,more=true,all=[];while(more){const p=await c.request('event/read',{session_id:sid,after});all.push(...p.events);after=p.cursor;more=p.has_more;}return all;}
const checks=[];let c;
try{
  c=new Client('default-state');let init=await c.start();assert.equal(init.execution.sandbox,'openshell');
  let sid=await create(c,'default');await c.request('turn/start',{session_id:sid,submission_id:'default',text:'Do the scripted task'});
  await until(()=>c.request('session/attach',{session_id:sid}),s=>s.turn.status==='failed','missing sandbox failure');
  assert.ok(!(await events(c,sid)).some(e=>e.type==='tool_started'));await c.stop();checks.push('default fails without OpenShell; no direct fallback');

  c=new Client('off-state','off');init=await c.start();assert.equal(init.execution.enforcement,'none');assert.equal(init.execution.sandbox,'off');
  sid=await create(c,'complete');await c.request('turn/start',{session_id:sid,submission_id:'complete',text:'Do the scripted task'});
  let snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>['completed','failed'].includes(s.turn.status),'off completion');
  assert.equal(snap.turn.status,'completed');assert.equal(snap.session.sandbox,'off');
  assert.ok((await readFile(join(root,'complete','proof.txt'),'utf8')).includes('Hello ExampleApp'));
  let log=await events(c,sid);assert.equal(log.filter(e=>e.type==='tool_started').length,3);
  assert.ok(log.filter(e=>e.type==='tool_started').every(e=>e.data.enforcement==='none'&&e.data.sandbox==='off'));
  assert.ok(!log.some(e=>e.type==='sandbox_creating'||e.type==='interaction_required'));
  await c.request('session/close',{session_id:sid});checks.push('off completes native loop without Docker/OpenShell; truthful enforcement');

  for(const stop of ['cancel','SIGKILL']){
    sid=await create(c,stop);const turn=(await c.request('turn/start',{session_id:sid,submission_id:stop,text:'TEST_DIRECT_HANG'})).turn;
    const childPid=await until(async()=>{try{return Number((await readFile(join(root,stop,'direct-child.pid'),'utf8')).trim());}catch{return null;}},v=>Number.isInteger(v)&&v>0,'detached child');
    if(stop==='cancel'){
      await c.request('turn/cancel',{session_id:sid,turn_id:turn.id});
      snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>['cancelled','failed'].includes(s.turn.status),'cancel completion');assert.equal(snap.turn.status,'cancelled');
    }else{
      await c.stop('SIGKILL');await absent(childPid);c=new Client('off-state','off');await c.start();
      snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.turn.status,'interrupted');
      assert.equal((await events(c,sid)).filter(e=>e.type==='tool_started').length,1);
    }
    await absent(childPid);await c.request('session/close',{session_id:sid});checks.push(stop+' reaps detached descendant; uncertain work not replayed');
  }

  sid=await create(c,'parked','ask',false);await c.request('turn/start',{session_id:sid,submission_id:'park',text:'Ask me first'});
  snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>s.interactions.length>0,'parked question');const iid=snap.interactions[0].id;
  await c.stop();c=new Client('off-state','openshell');await c.start();
  await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:iid,answer:'Hello ExampleApp'}),/backend differs/);
  snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,iid);await c.stop();
  c=new Client('off-state','off');await c.start();snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,iid);
  await c.request('turn/cancel',{session_id:sid,turn_id:snap.turn.id});await c.request('session/close',{session_id:sid});checks.push('EOF preserves question; backend mismatch cannot consume approval/answer');
  await writeFile(join(root,'checks.json'),JSON.stringify(checks,null,2),{mode:0o600});
  console.log(JSON.stringify({result:'PASS',state:root,checks}));
}finally{if(c)await c.stop().catch(()=>{});}
