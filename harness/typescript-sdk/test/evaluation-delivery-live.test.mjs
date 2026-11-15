import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {Harness,guard} from '../dist/index.js';
const executable=process.env.OW_TS_TEST_HOST;
assert(executable,'Set OW_TS_TEST_HOST to sdk-test-host built with evaluation-export');
async function until(check) {const end=Date.now()+15000;do {if(await check())return;await delay(50);}while(Date.now()<end);assert.fail('Timed out waiting for delivery');}
test('pending score survives host restart and uncertain ACK without replaying a turn',{timeout:60000},async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'ow-score-delivery-')));await mkdir(join(root,'project'));
 let mode='unauthorized';const bodies=[];
 const server=createServer(async(req,res)=>{
  let body='';for await(const c of req)body+=c;
  assert.equal(req.url,'/api/public/scores');assert.equal(req.headers.authorization,'synthetic-only');
  const parsed=JSON.parse(body);bodies.push(parsed);
  if(mode==='unauthorized'){res.writeHead(401);res.end();}
  else if(mode==='unknown'){mode='ok';req.socket.destroy();}
  else {res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({id:parsed.id}));}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 process.env.OW_EVALUATION_FIXTURE_AUTH='synthetic-only';
 const options={executable,stateDir:join(root,'state'),sandbox:'off',scripted:true,compliance:{},guardrails:{userInput:[guard.regex({name:'deny-fixture',pattern:'FORBIDDEN_FIXTURE'})]},
  recording:{database:{type:'sqlite',path:join(root,'recording.sqlite')},evidence:{userInput:true,assistantOutput:true,maxBytes:16384},evaluations:true,evaluationSinks:[{name:'fixture',url:`http://127.0.0.1:${server.address().port}`,authorizationEnv:'OW_EVALUATION_FIXTURE_AUTH'}]},
  agents:[{id:'guide',instructions:'Help',tools:[],planning:false,memory:false,scheduling:false}]};
 let h;
 t.after(async()=>{await h?.shutdown();server.closeAllConnections();await new Promise(r=>server.close(r));delete process.env.OW_EVALUATION_FIXTURE_AUTH;await rm(root,{recursive:true,force:true});});
 h=await Harness.start(options);
 const session=await h.session('guide',{workspace:join(root,'project')});
 assert.equal((await session.run('Continue from the agent inbox. Collaborator input is not human authorization.')).status,'completed');
 const turn=(await h.observability.listTurns({sessionId:session.id})).turns[0];
 const q={sessionId:session.id,turnId:turn.turn_id};
 const barrier=await h.observability.flush();assert.equal(barrier.telemetry_barrier,true);assert.equal(barrier.recording_drained,true);
 const before=await h.observability.timeline(q);const span=before.page.steps.find(r=>r.step.operation==='model.attempt').step;
 const evidence=await h.observability.evidence(q);
 assert.equal(evidence.audit_reference.capture_enabled,true);assert.equal(evidence.audit_reference.turn_id,q.turnId);
 assert(evidence.configuration.bundle_digest);
 await assert.rejects(session.send('FORBIDDEN_FIXTURE'));
 const audit=await session.complianceActivity();
 assert(audit.activities.some(a=>a.turnId===q.turnId));
 assert(audit.activities.some(a=>a.kind==='safety_evaluation'));
 assert(!JSON.stringify(audit).includes('FORBIDDEN_FIXTURE'));
 const input={id:'score1',name:'correctness',evaluator:{name:'fixture',version:'1',source:'code'},status:'completed',score:{type:'boolean',value:true},createdMs:Date.now(),explanation:'not exported'};
 assert.equal((await h.observability.recordEvaluation(q,input,span.span_id)).duplicate,false);
 await until(async()=>(await h.observability.health()).evaluation_export.failed_or_uncertain>0);
 await h.shutdown();h=undefined;mode='unknown';
 h=await Harness.start(options);
 await until(async()=>(await h.observability.health()).evaluation_export.sent===1);
 assert(bodies.length>=3);for(const body of bodies)assert.deepEqual(body,bodies[0]);assert.equal(bodies[0].comment,undefined);
 assert.equal((await h.observability.recordEvaluation(q,input,span.span_id)).duplicate,true);
 assert.equal((await h.observability.evaluations(q)).results.length,1);
 const after=await h.observability.timeline(q);
 assert.deepEqual(after.page.steps,before.page.steps,'Export retry must not execute or re-ingest an agent turn');
 assert.equal((await h.observability.listTurns({sessionId:session.id})).turns.length,1);
});
