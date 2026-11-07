// Scripted provider, real Linux host + OpenShell processes. SYNTHETIC secrets only.
// Node is a test driver, not a shipped runtime dependency. No screen capture.
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile,writeFile,readdir,access,unlink} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import assert from 'node:assert/strict';

const binary=process.env.OPENWORKER_BIN||resolve('target/debug/openworker');
const root=resolve(process.env.GRANTS_TEST_OUTPUT||'test-output','grants-'+randomUUID());
const state=join(root,'state'),bundle=resolve('examples/mixed');
for(const dir of ['projects','notes','exports','secrets','host-only'])await mkdir(join(root,dir),{recursive:true,mode:0o700});
const token='SYNTHETIC_'+randomUUID();
await writeFile(join(root,'secrets/token'),token,{mode:0o600});
await writeFile(join(root,'secrets/unselected'),'UNSELECTED_SYNTHETIC',{mode:0o600});
await writeFile(join(root,'notes/reference'),'REFERENCE');
await writeFile(join(root,'host-only/sentinel'),'HOST_ONLY');
// Controlled HTTP fixtures; no real remote services or accounts.
const host=process.env.GRANTS_FIXTURE_HOST||execFileSync('hostname',['-I'],{encoding:'utf8'}).trim().split(/\s+/)[0];
let goodHits=0,badHits=0,redirectHits=0;
const good=createServer((req,res)=>{if(req.url==='/redirect'){redirectHits++;res.writeHead(302,{Location:`http://${host}:${bad.address().port}/`});res.end();return;}goodHits++;res.writeHead(req.headers.authorization==='Bearer '+token?200:401);res.end(req.headers.authorization==='Bearer '+token?'AUTH_OK':'NO');});
const bad=createServer((req,res)=>{badHits++;res.end('SHOULD_NOT_BE_REACHED');});
await new Promise(r=>good.listen(0,'0.0.0.0',r));await new Promise(r=>bad.listen(0,'0.0.0.0',r));
const goodPort=good.address().port,badPort=bad.address().port;
assert.equal(await (await fetch(`http://${host}:${goodPort}/`,{headers:{Authorization:'Bearer '+token}})).text(),'AUTH_OK');
assert.equal(await (await fetch(`http://${host}:${badPort}/`)).text(),'SHOULD_NOT_BE_REACHED');
goodHits=0;badHits=0;
const identity=JSON.parse(execFileSync(binary,['inspect-bundle','--bundle',bundle],{encoding:'utf8'}));
const document={version:1,bundle_digest:identity.bundle_digest,profiles:{daily:{sandbox:'openshell',workspace_roots:[join(root,'projects')],
  servers:{desktop:{placement:'host'},reports:{placement:'sandbox'}},
  files:{reference:{path:join(root,'notes'),access:'read'},output:{path:join(root,'exports'),access:'read_write'}},
  network:{api:{host,port:goodPort}},
  credentials:{token:{version:'v1',source_file:join(root,'secrets/token'),delivery:'sandbox_file',target:'/run/openworker/credentials/token',requires_network:['api']},
    unselected:{version:'v1',source_file:join(root,'secrets/unselected'),delivery:'sandbox_file',target:'/run/openworker/credentials/unselected'}}}}};
const policyFile=join(root,'runtime-policy.json');const originalPolicy=JSON.stringify(document);await writeFile(policyFile,originalPolicy,{mode:0o600});
const selection={profile:'daily',files:['reference','output'],network:['api'],credentials:['token']};
let seq=0;
class Client {
  pending=new Map();
  async start(){
    this.child=spawn(binary,['serve','--stdio','--scripted','--state-dir',state,'--bundle',bundle,'--runtime-policy',policyFile],{env:{...process.env,OPENAI_API_KEY:'SYNTHETIC_CONTROLLER_KEY_MUST_NOT_LEAK'},stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(r=>this.child.once('exit',r));
    createInterface({input:this.child.stdout}).on('line',line=>{const m=JSON.parse(line);const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Timeout '+method));},90000);this.pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');});}
  async stop(signal){if(!this.child||this.child.exitCode!==null||this.child.signalCode)return;if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
async function until(read,test,label){const end=Date.now()+240000;while(Date.now()<end){const v=await read();if(test(v))return v;await new Promise(r=>setTimeout(r,150));}throw Error('Timed out '+label);}
async function events(c,sid){let after=0,more=true,log=[];while(more){const p=await c.request('event/read',{session_id:sid,after});log.push(...p.events);after=p.cursor;more=p.has_more;}return log;}
async function approve(c,sid,i){await until(async()=>{try{return await c.request('interaction/respond',{session_id:sid,interaction_id:i.id,decision:'once'});}catch(e){if(e.message.includes('checkpointing'))return null;throw e;}},Boolean,'approval cleanup');}
async function cleanCopies(){let children=[];try{children=await readdir(join(state,'credential-files'));}catch(e){if(e.code!=='ENOENT')throw e;}return children.length===0;}
async function create(c,name,mode='ask',grants=selection){const workspace=join(root,'projects',name);await mkdir(workspace);const p={coworker:'mixed-example',workspace,policy:grants};const preview=(await c.request('policy/resolve',p)).policy;assert.equal(preview.servers.desktop,'host');assert.equal(preview.servers.reports,'sandbox');assert.ok(!JSON.stringify(preview).includes(token));assert.ok(await cleanCopies(),'preview must not copy secrets');await assert.rejects(c.request('session/create',{...p,name,permissions:{mode}}),/digest/);const sid=(await c.request('session/create',{...p,name,permissions:{mode},expected_policy_digest:preview.digest})).session.id;return {sid,workspace};}
function steps(command){return [{name:'desktop_status',arguments:{text:'Synthetic desktop integration'}},{name:'host_probe',arguments:{command:`test -f '${join(root,'host-only/sentinel')}' && test -z "$OPENAI_API_KEY" && printf HOST_OK`}},{name:'sandbox_exec',arguments:{command}}];}
const command=`set -eu
test -z "\${OPENAI_API_KEY-}"
test "$(cat '${join(root,'notes/reference')}')" = REFERENCE
if printf bad > '${join(root,'notes/reference')}' 2>/dev/null; then exit 21; fi
printf OUTPUT_OK > '${join(root,'exports/result')}'
test ! -e '${join(root,'host-only/sentinel')}'
test ! -e '${join(root,'secrets/token')}'
test ! -e /run/openworker/credentials/unselected
test -s /run/openworker/credentials/token
if printf bad > /run/openworker/credentials/token 2>/dev/null; then exit 22; fi
curl -fsS --max-time 8 -H "Authorization: Bearer $(cat /run/openworker/credentials/token)" 'http://${host}:${goodPort}/' > response.txt
test "$(cat response.txt)" = AUTH_OK
if curl -fsS --max-time 5 'http://${host}:${badPort}/' >/dev/null 2>&1; then exit 23; fi
if curl -fLsS --max-time 5 'http://${host}:${goodPort}/redirect' >/dev/null 2>&1; then exit 24; fi
if curl --noproxy '*' -fsS --max-time 5 'http://${host}:${goodPort}/' >/dev/null 2>&1; then exit 25; fi
printf GRANTS_OK`;
const reports=[];let c=new Client();
try{
  const init=await c.start();assert.equal(init.capabilities.resource_policy,true);
  for(const mode of ['ask','bypass']){
    console.error('grants: starting '+mode+' mixed session');
    const {sid}=await create(c,mode,mode);await c.request('turn/start',{session_id:sid,submission_id:mode,text:'TEST_RESOURCE_GRANTS '+JSON.stringify(steps(command))});
    let restarted=false;
    await until(async()=>{
      let snap=await c.request('session/attach',{session_id:sid});
      if(['failed','interrupted','cancelled'].includes(snap.turn.status))throw Error(JSON.stringify(await events(c,sid)));
      if(snap.interactions.length){const i=snap.interactions[0];assert.equal(i.resource_policy_digest,snap.session.resource_policy.digest);
        if(!restarted){
          // Wait for owned processes and copied files to stop before testing restart.
          await until(cleanCopies,Boolean,'parked credential cleanup');
          const old=i.id;await c.stop('SIGKILL');c=new Client();await c.start();snap=await c.request('session/attach',{session_id:sid});assert.equal(snap.interactions[0].id,old);restarted=true;
          await writeFile(policyFile,originalPolicy+' ');
          await assert.rejects(c.request('interaction/respond',{session_id:sid,interaction_id:old,decision:'once'}),/policy changed/);
          await writeFile(policyFile,originalPolicy);
        }
        await approve(c,sid,i);
      }
      return snap;
    },s=>s.turn.status==='completed','mixed '+mode);
    await until(cleanCopies,Boolean,'finished credential cleanup');
    const log=await events(c,sid);const started=log.filter(e=>e.type==='tool_started');assert.equal(started.length,3);
    assert.equal(started.find(e=>e.data.name==='host_probe').data.enforcement,'none');assert.equal(started.find(e=>e.data.name==='sandbox_exec').data.enforcement,'full');
    const result=JSON.parse(log.find(e=>e.type==='tool_finished'&&e.data.name==='sandbox_exec').data.result.content[0].text);assert.equal(result.exit_code,0,JSON.stringify(result));assert.match(result.stdout,/GRANTS_OK/);
    assert.ok(!JSON.stringify(log).includes(token),'credential bytes must not be inserted into audit');
    for(const e of log.filter(e=>e.type==='direct_process_registered'))await assert.rejects(access('/proc/'+e.data.pid));
    await writeFile(join(root,mode+'-events.json'),JSON.stringify(log,null,2));await c.request('session/close',{session_id:sid});reports.push(mode+' mixed routing, scope enforcement, credential use and cleanup');
  }
  assert.equal(goodHits,2);assert.equal(badHits,0);assert.equal(redirectHits,2);
  assert.equal(await readFile(join(root,'notes/reference'),'utf8'),'REFERENCE');assert.equal(await readFile(join(root,'exports/result'),'utf8'),'OUTPUT_OK');
  // Omitting a grant really withholds its mount, endpoint and copied secret.
  const empty=await create(c,'empty','bypass',{profile:'daily'});
  const noGrants=`set -eu; test ! -e /run/openworker/credentials/token; test ! -e '${join(root,'notes/reference')}'; if curl -fsS --max-time 5 'http://${host}:${goodPort}/' >/dev/null 2>&1; then exit 24; fi; printf EMPTY_OK`;
  await c.request('turn/start',{session_id:empty.sid,submission_id:'empty',text:'TEST_RESOURCE_GRANTS '+JSON.stringify([{name:'sandbox_exec',arguments:{command:noGrants}}])});
  const done=await until(()=>c.request('session/attach',{session_id:empty.sid}),s=>['completed','failed'].includes(s.turn.status),'empty grants');assert.equal(done.turn.status,'completed');
  const emptyLog=await events(c,empty.sid);assert.equal(JSON.parse(emptyLog.find(e=>e.type==='tool_finished').data.result.content[0].text).exit_code,0);assert.equal(goodHits,2);await c.request('session/close',{session_id:empty.sid});reports.push('unselected grants withheld even in Bypass');
  // Credential rotation invalidates a durable approval, including after restart.
  const rotating=await create(c,'rotation');await c.request('turn/start',{session_id:rotating.sid,submission_id:'rotation',text:'TEST_RESOURCE_GRANTS '+JSON.stringify(steps(command))});
  const pending=await until(()=>c.request('session/attach',{session_id:rotating.sid}),s=>s.interactions.length||s.turn.status==='failed','rotation pending');assert.equal(pending.turn.status,'waiting');await until(cleanCopies,Boolean,'rotation cleanup');
  await writeFile(join(root,'secrets/token'),'ROTATED_SYNTHETIC',{mode:0o600});
  await assert.rejects(c.request('interaction/respond',{session_id:rotating.sid,interaction_id:pending.interactions[0].id,decision:'once'}),/identity changed/);
  await c.stop();c=new Client();await c.start();await assert.rejects(c.request('interaction/respond',{session_id:rotating.sid,interaction_id:pending.interactions[0].id,decision:'once'}),/identity changed/);
  assert.ok(!(await events(c,rotating.sid)).some(e=>e.type==='tool_started'));
  await c.request('turn/cancel',{session_id:rotating.sid,turn_id:pending.turn.id});
  await until(()=>c.request('session/attach',{session_id:rotating.sid}),s=>s.turn.status==='cancelled','rotated pending turn cancellation');
  await c.request('session/close',{session_id:rotating.sid});reports.push('credential rotation refuses old approval before and after restart');
  const provisioning=await create(c,'provision-cancel','bypass');
  const provisioningTurn=await c.request('turn/start',{session_id:provisioning.sid,submission_id:'provision-cancel',text:'TEST_RESOURCE_GRANTS '+JSON.stringify(steps(command))});
  await c.request('turn/cancel',{session_id:provisioning.sid,turn_id:provisioningTurn.turn.id});
  await until(()=>c.request('session/attach',{session_id:provisioning.sid}),s=>s.turn.status==='cancelled','provisioning cancellation');
  assert.ok(!(await events(c,provisioning.sid)).some(e=>e.type==='tool_started'));assert.ok(await cleanCopies());
  await c.request('session/close',{session_id:provisioning.sid});reports.push('cancel immediately after acceptance cleans provisioning without dispatch');
  for(const lifecycle of ['cancel','eof','crash']){
    console.error('grants: '+lifecycle+' during sandbox execution');
    const item=await create(c,lifecycle,'bypass');
    const start=await c.request('turn/start',{session_id:item.sid,submission_id:lifecycle,text:'TEST_RESOURCE_GRANTS '+JSON.stringify([{name:'sandbox_exec',arguments:{command:'sleep 30; printf MUST_NOT_FINISH'}}])});
    await until(()=>events(c,item.sid),log=>log.some(e=>e.type==='tool_started'),'active tool');
    assert.equal(await cleanCopies(),false,'active sandbox has selected credential copy');
    if(lifecycle==='cancel'){
      await c.request('turn/cancel',{session_id:item.sid,turn_id:start.turn.id});
      await until(()=>c.request('session/attach',{session_id:item.sid}),s=>s.turn.status==='cancelled','cancelled');
    }else{
      await c.stop(lifecycle==='crash'?'SIGKILL':undefined);
      if(lifecycle==='crash')await unlink(join(root,'secrets/token'));
      c=new Client();await c.start();
      const snap=await c.request('session/attach',{session_id:item.sid});assert.equal(snap.turn.status,'interrupted');
      if(lifecycle==='crash')await writeFile(join(root,'secrets/token'),'ROTATED_SYNTHETIC',{mode:0o600});
    }
    await until(cleanCopies,Boolean,lifecycle+' credential cleanup');
    const log=await events(c,item.sid);assert.equal(log.filter(e=>e.type==='tool_started').length,1);assert.ok(!log.some(e=>e.type==='tool_finished'));
    await c.request('session/close',{session_id:item.sid});reports.push(lifecycle+' cleanup with no replay'+(lifecycle==='crash'?' even with missing credential source':''));
  }
  await c.stop();assert.ok(await cleanCopies());
  assert.equal((await readdir(join(state,'sandbox-files'))).length,0,'generated runner copies must also be reclaimed');
  // Raw synthetic token must not appear in durable DB or runtime-generated files.
  assert.ok(!(await readFile(join(state,'runtime.sqlite3'))).includes(Buffer.from(token)));
  await writeFile(join(root,'report.json'),JSON.stringify({reports,goodHits,badHits,root},null,2));
  console.log(JSON.stringify({passed:true,reports,root},null,2));
}finally{await c.stop();await new Promise(r=>good.close(r));await new Promise(r=>bad.close(r));}
