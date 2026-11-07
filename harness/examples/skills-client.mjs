// Bundled-skill feature driver: scripted model, real native runtime/MCP/backend.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,cp,readFile,writeFile,access} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const sandbox=process.env.OPENWORKER_SANDBOX||'openshell';
const root=resolve('test-output','skills-'+sandbox+'-'+randomUUID());await mkdir(root,{recursive:true});
const bundle=join(root,'bundle');await cp(resolve('examples/skilled'),bundle,{recursive:true});
let seq=0;
class Client {
  pending=new Map();
  async start(){
    this.child=spawn(binary,['serve','--stdio','--scripted','--sandbox',sandbox,'--state-dir',join(root,'state'),'--bundle',bundle],{env:{...process.env,OPENAI_API_KEY:''},stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(r=>this.child.once('exit',r));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('RPC timeout '+method));},30000);this.pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');});}
  async stop(signal){if(!this.child||this.child.exitCode!==null||this.child.signalCode)return;if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
async function until(read,predicate,label){const end=Date.now()+240000;while(Date.now()<end){const v=await read();if(predicate(v))return v;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out '+label);}
async function events(sid){let after=0,more=true,all=[];while(more){const p=await c.request('event/read',{session_id:sid,after});all.push(...p.events);after=p.cursor;more=p.has_more;}return all;}
async function create(name,mode='ask',skills){const workspace=join(root,name);await mkdir(workspace);const p={coworker:'skilled-example',name,workspace,permissions:{mode}};if(skills!==undefined)p.skills=skills;return {sid:(await c.request('session/create',p)).session.id,workspace};}
async function start(sid){return c.request('turn/start',{session_id:sid,submission_id:'build',text:'TEST_SKILLS'});}
async function parked(sid,alias){const s=await until(()=>c.request('session/attach',{session_id:sid}),v=>v.interactions.length||['completed','failed'].includes(v.turn.status),'park '+alias);assert.equal(s.turn.status,'waiting',JSON.stringify(await events(sid)));assert.equal(s.interactions[0].call.name,alias);return s;}
async function respond(sid,i,decision='once'){await until(async()=>{try{return await c.request('interaction/respond',{session_id:sid,interaction_id:i.id,decision});}catch(e){if(e.message.includes('checkpointing'))return null;throw e;}},Boolean,'approval');}
async function terminal(sid,status){const s=await until(()=>c.request('session/attach',{session_id:sid}),v=>['completed','failed','cancelled'].includes(v.turn.status),'terminal '+status);assert.equal(s.turn.status,status,JSON.stringify(await events(sid)));return s;}
async function close(sid){await c.request('session/close',{session_id:sid});}
let c=new Client();const checks=[];
try{
  const init=await c.start();assert.equal(init.capabilities.bundled_skills,true);
  const list=await c.request('coworker/list',{});assert.equal(list.coworkers[0].package.schema_version,1);
  assert.deepEqual(list.coworkers[0].skills.map(s=>s.name),['report-guide']);
  assert.ok(!JSON.stringify(list).includes('REPORT_GUIDE_BODY_MARKER'));
  let {sid,workspace}=await create('main');
  assert.deepEqual((await c.request('skills/list',{session_id:sid})).loaded,[]);
  await start(sid);let snap=await parked(sid,'load_skill');let i=snap.interactions[0];assert.equal(i.server,null);
  const skillPath=join(bundle,'coworker/skills/report-guide/SKILL.md');const original=await readFile(skillPath,'utf8');
  await writeFile(skillPath,original+'\nChanged instructions.\n');
  await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:i.id,decision:'once'}),/bundle changed/i);
  assert.ok(!(await events(sid)).some(e=>e.type==='tool_started'));await writeFile(skillPath,original);
  await c.stop('SIGKILL');c=new Client();await c.start();snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,i.id);
  await respond(sid,i);snap=await parked(sid,'read_skill_resource');i=snap.interactions[0];
  assert.deepEqual(snap.session.loaded_skills,['report-guide']);
  await c.stop();c=new Client();await c.start();snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,i.id);assert.deepEqual(snap.session.loaded_skills,['report-guide']);
  await respond(sid,i);snap=await parked(sid,'write_file');await assert.rejects(access(join(workspace,'skill-proof.txt')));
  await respond(sid,snap.interactions[0]);await terminal(sid,'completed');
  assert.match(await readFile(join(workspace,'skill-proof.txt'),'utf8'),/Bundled skill loaded/);
  let log=await events(sid);const calls=log.filter(e=>e.type==='tool_started');assert.equal(calls.length,3);
  assert.equal(calls[0].data.enforcement,'bundle-read-only');assert.equal(calls[1].data.enforcement,'bundle-read-only');
  assert.equal(calls[2].data.enforcement,sandbox==='off'?'none':'full');
  const loaded=log.find(e=>e.type==='tool_finished'&&e.data.name==='load_skill').data.result;
  assert.match(loaded.instructions,/REPORT_GUIDE_BODY_MARKER/);assert.equal(loaded.source,'coworker_bundle');
  assert.ok(log.find(e=>e.type==='tool_finished'&&e.data.name==='read_skill_resource').data.result.content.includes('SKILL_REFERENCE_MARKER'));
  assert.equal(log.filter(e=>e.type==='interaction_required').length,3,'Skill tool hints cannot approve later work');
  await writeFile(join(root,'main-events.json'),JSON.stringify(log,null,2),{mode:0o600});await close(sid);
  checks.push('lazy catalog, approved load/resource/write, package mutation refusal, SIGKILL and EOF preserve skill/approval identity');

  ({sid}=await create('disabled','bypass',[]));assert.deepEqual((await c.request('skills/list',{session_id:sid})).skills,[]);
  await start(sid);await terminal(sid,'failed');log=await events(sid);assert.ok(!log.some(e=>e.type==='tool_started'));
  assert.deepEqual((await c.request('session/attach',{session_id:sid})).session.loaded_skills,[]);await close(sid);
  checks.push('explicit empty selection removes skill tools; stale model call fails without execution');

  ({sid}=await create('denied'));await start(sid);snap=await parked(sid,'load_skill');await respond(sid,snap.interactions[0],'deny');
  snap=await parked(sid,'read_skill_resource');assert.deepEqual(snap.session.loaded_skills,[]);
  log=await events(sid);assert.ok(log.find(e=>e.type==='tool_finished'&&e.data.name==='load_skill').data.result.denied);
  assert.ok(!JSON.stringify(log).includes('REPORT_GUIDE_BODY_MARKER'));
  await c.request('turn/cancel',{session_id:sid,turn_id:snap.turn.id});await terminal(sid,'cancelled');await close(sid);
  checks.push('denying load never exposes instructions or records successful loading');

  ({sid,workspace}=await create('bypass','bypass'));await start(sid);snap=await terminal(sid,'completed');assert.deepEqual(snap.session.loaded_skills,['report-guide']);
  log=await events(sid);assert.ok(!log.some(e=>e.type==='interaction_required'));assert.equal(log.filter(e=>e.type==='tool_started').length,3);await close(sid);
  checks.push('bypass skips approval without changing selected skills or backend');
  await writeFile(join(root,'checks.json'),JSON.stringify(checks,null,2),{mode:0o600});
  console.log(JSON.stringify({result:'PASS',sandbox,state:root,checks}));
}finally{await c.stop().catch(()=>{});}
