// Feature/regression driver, not the deferred partner acceptance suite.
// Scripted model, real runtime and MCP processes. Run on Linux under both backends.
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,cp,chmod,readFile,writeFile,access} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const sandbox=process.env.OPENWORKER_SANDBOX||'openshell';
const root=resolve('test-output','multi-'+sandbox+'-'+randomUUID());
await mkdir(root,{recursive:true});
let sequence=0;
class Client {
  pending=new Map();
  constructor(state,bundle,policy){Object.assign(this,{state,bundle,policy});}
  async start(){
    this.child=spawn(binary,['serve','--stdio','--scripted','--state-dir',this.state,'--bundle',this.bundle,'--sandbox',sandbox,...(this.policy?['--tool-policy',this.policy]:[])],{env:{...process.env,OPENAI_API_KEY:''},stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(r=>this.child.once('exit',r));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{
    const id=++sequence;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('RPC timeout '+method));},30000);
    this.pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});
    this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });}
  async stop(signal){if(!this.child||this.child.exitCode!==null||this.child.signalCode)return;if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
async function until(read,predicate,label){const end=Date.now()+240000;while(Date.now()<end){const v=await read();if(predicate(v))return v;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out: '+label);}
async function events(c,sid){let after=0,more=true,result=[];while(more){const p=await c.request('event/read',{session_id:sid,after});result.push(...p.events);after=p.cursor;more=p.has_more;}return result;}
async function create(c,name,mode='ask',markers=[]){
  const workspace=join(root,name);await mkdir(workspace);
  for(const marker of markers)await writeFile(join(workspace,marker),'synthetic test marker');
  const sid=(await c.request('session/create',{coworker:'multi-example',workspace,name,permissions:{mode,auto_skip_questions:true}})).session.id;
  await c.request('turn/start',{session_id:sid,submission_id:name,text:'TEST_MULTI_MCP'});return {sid,workspace};
}
async function parked(c,sid){
  const s=await until(()=>c.request('session/attach',{session_id:sid}),v=>v.interactions.length||['failed','completed'].includes(v.turn.status),'parked approval');
  assert.equal(s.turn.status,'waiting',JSON.stringify(await events(c,sid)));return s;
}
async function respond(c,sid,i){
  await until(async()=>{try{return await c.request('interaction/respond',{session_id:sid,interaction_id:i.id,decision:'once'});}catch(e){if(e.message.includes('checkpointing'))return null;throw e;}},Boolean,'approval checkpoint');
}
async function clean(c,sid,log){
  await c.request('session/close',{session_id:sid});
  if(sandbox==='off'){
    for(const e of log.filter(e=>e.type==='direct_process_registered')){
      await until(async()=>{try{await access('/proc/'+e.data.pid);return false;}catch{return true;}},Boolean,'supervisor exit');
    }
  }else{
    const all=await events(c,sid);
    for(const e of all.filter(e=>e.type==='sandbox_creating'))assert.ok(all.some(x=>x.type==='sandbox_closed'&&x.data.name===e.data.name));
  }
}
const checks=[];let c;
try{
  const bundle=resolve('examples/multi');
  const inspected=JSON.parse(execFileSync(binary,['inspect-bundle','--bundle',bundle],{encoding:'utf8'}));
  assert.deepEqual(inspected.servers,['north','south']);
  const policy=join(root,'tool-policy.json');
  const document={version:1,bundle_digest:inspected.bundle_digest,overrides:[{server:'north',tool:'write_file',category:'read'}]};
  const bytes=JSON.stringify(document);await writeFile(policy,bytes,{mode:0o600});
  const state=join(root,'state');c=new Client(state,bundle,policy);
  const init=await c.start();assert.equal(init.capabilities.multi_mcp_servers,true);
  let {sid,workspace}=await create(c,'routing');
  let snap=await parked(c,sid);const first=snap.interactions[0];
  assert.equal(first.server,'north');assert.equal(first.remote_tool,'example_echo');
  assert.equal(snap.session.tool_policy.tools.north_write.category,'read');
  assert.equal(snap.session.tool_policy.tools.north_write.base_category,'write_local');
  assert.equal(snap.session.tool_policy.tools.north_write.source,'host_policy');
  assert.equal(first.policy_digest,snap.session.tool_policy.digest);
  // Neither a changed file in the running process nor reload under a new digest
  // may consume the pending approval. Inspection remains available.
  document.overrides[0].category='execute';await writeFile(policy,JSON.stringify(document));
  await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:first.id,decision:'once'}),/policy changed/i);
  await c.stop('SIGKILL');c=new Client(state,bundle,policy);await c.start();
  await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:first.id,decision:'once'}),/identity changed/i);
  snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,first.id);
  assert.ok(!(await events(c,sid)).some(e=>e.type==='tool_started'));
  await c.stop();await writeFile(policy,bytes);
  const misplaced=join(workspace,'same-policy.json');await writeFile(misplaced,bytes);
  c=new Client(state,bundle,misplaced);await c.start();
  await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:first.id,decision:'once'}),/inside the workspace/);
  await c.stop();c=new Client(state,bundle,policy);await c.start();
  snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,first.id);
  await respond(c,sid,first);
  for(const [alias,server,tool] of [['south_echo','south','example_echo'],['north_write','north','write_file']]){
    snap=await parked(c,sid);const i=snap.interactions[0];assert.equal(i.call.name,alias);assert.equal(i.server,server);assert.equal(i.remote_tool,tool);await respond(c,sid,i);
  }
  snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>['completed','failed'].includes(s.turn.status),'completed routing');
  assert.equal(snap.turn.status,'completed');assert.match(await readFile(join(workspace,'multi-proof.txt'),'utf8'),/Two MCP servers/);
  let log=await events(c,sid);
  for(const side of ['north','south']){
    const result=log.find(e=>e.type==='tool_finished'&&e.data.name===side+'_echo').data.result;
    assert.equal(JSON.parse(result.content[0].text).server_label,side);
  }
  assert.equal(log.filter(e=>e.type==='tool_started').length,3);
  assert.equal(log.filter(e=>e.type==='interaction_required').length,3,'Read override must not silently approve');
  await writeFile(join(root,'routing-events.json'),JSON.stringify(log,null,2),{mode:0o600});
  await clean(c,sid,log);await c.stop();checks.push('exact same-name routing, read still asks, durable approval restart and changed-policy refusal');

  // First server starts successfully; second fails initialize. Both must stop.
  const broken=join(root,'broken-bundle');await cp(bundle,broken,{recursive:true});
  let tools=JSON.parse(await readFile(join(broken,'tools.json'),'utf8'));tools.servers[1].args.push('--invalid-test-option');
  await writeFile(join(broken,'tools.json'),JSON.stringify(tools));
  c=new Client(join(root,'broken-state'),broken);await c.start();({sid}=await create(c,'partial-start','bypass'));
  snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>s.turn.status==='failed','partial startup failure');
  log=await events(c,sid);assert.ok(!log.some(e=>e.type==='tool_started'));
  if(sandbox==='off')assert.equal(log.filter(e=>e.type==='direct_process_registered').length,2);
  await clean(c,sid,log);await c.stop();await writeFile(join(root,'partial-events.json'),JSON.stringify(log,null,2),{mode:0o600});
  checks.push('partial multi-server startup fails closed and cleans already-started servers');

  // Third-party tool self-declares readOnlyHint. It stays External. Its observed
  // catalog changes after an approval is parked, without executable changes.
  const dynamic=join(root,'dynamic-bundle');await cp(bundle,dynamic,{recursive:true});
  await cp(resolve('tests/fixtures/mcp-catalog.sh'),join(dynamic,'catalog.sh'));await chmod(join(dynamic,'catalog.sh'),0o700);
  tools=JSON.parse(await readFile(join(dynamic,'tools.json'),'utf8'));
  tools.servers[0].program='catalog.sh';tools.servers[0].args=['$WORKSPACE'];delete tools.bindings.north_write;
  await writeFile(join(dynamic,'tools.json'),JSON.stringify(tools));
  c=new Client(join(root,'dynamic-state'),dynamic);await c.start();({sid,workspace}=await create(c,'catalog-change'));
  snap=await parked(c,sid);assert.equal(snap.session.tool_policy.tools.north_echo.category,'external');
  await writeFile(join(workspace,'catalog-changed'),'synthetic test marker');
  await respond(c,sid,snap.interactions[0]);
  snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>s.turn.status==='failed','catalog mismatch');
  log=await events(c,sid);assert.ok(log.some(e=>e.type==='turn_completed'&&e.data.status==='failed'&&e.data.error.includes('catalogs/routes changed')));
  assert.ok(!log.some(e=>e.type==='tool_started'));await assert.rejects(access(join(workspace,'unexpected-execution')));
  await clean(c,sid,log);await c.stop();await writeFile(join(root,'catalog-events.json'),JSON.stringify(log,null,2),{mode:0o600});
  checks.push('unknown external default ignores hints; changed catalog cannot execute parked approval');

  const hanging=join(root,'hanging-bundle');await cp(bundle,hanging,{recursive:true});
  await cp(resolve('tests/fixtures/mcp-catalog.sh'),join(hanging,'catalog.sh'));await chmod(join(hanging,'catalog.sh'),0o700);
  tools=JSON.parse(await readFile(join(hanging,'tools.json'),'utf8'));
  tools.servers[1].program='catalog.sh';tools.servers[1].args=['$WORKSPACE'];
  await writeFile(join(hanging,'tools.json'),JSON.stringify(tools));
  c=new Client(join(root,'hanging-state'),hanging);await c.start();({sid,workspace}=await create(c,'cancel-discovery','bypass',['catalog-hang']));
  await until(async()=>{try{await access(join(workspace,'catalog-started'));return true;}catch{return false;}},Boolean,'second server discovery');
  snap=await c.request('session/attach',{session_id:sid});
  await c.request('turn/cancel',{session_id:sid,turn_id:snap.turn.id});
  snap=await until(()=>c.request('session/attach',{session_id:sid}),s=>['cancelled','failed'].includes(s.turn.status),'multi-server cancellation');
  assert.equal(snap.turn.status,'cancelled');log=await events(c,sid);assert.ok(!log.some(e=>e.type==='tool_started'));
  if(sandbox==='off')assert.equal(log.filter(e=>e.type==='direct_process_registered').length,2);
  await clean(c,sid,log);await c.stop();await writeFile(join(root,'cancel-events.json'),JSON.stringify(log,null,2),{mode:0o600});
  checks.push('cancel during second server discovery stops both servers and their descendants');
  await writeFile(join(root,'checks.json'),JSON.stringify(checks,null,2),{mode:0o600});
  console.log(JSON.stringify({result:'PASS',sandbox,state:root,checks}));
}finally{if(c)await c.stop().catch(()=>{});}
