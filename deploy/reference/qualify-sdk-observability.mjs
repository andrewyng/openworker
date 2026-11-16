// Actual managed SDK, deterministic model, persistent recording and local Langfuse.
// Credentials are supplied explicitly and never written into evidence output.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {Harness,langfuseTelemetry} from '../../harness/typescript-sdk/dist/index.js';
const [executable,url,credentialFile,outputFile]=process.argv.slice(2);
assert(executable&&url&&credentialFile&&outputFile,'Usage: qualify-sdk-observability.mjs HOST LOCAL_LANGFUSE_URL CREDENTIAL_JSON OUTPUT_JSON');
const endpoint=new URL(url);assert(['127.0.0.1','localhost'].includes(endpoint.hostname)&&endpoint.protocol==='http:','Qualification requires local loopback Langfuse');
const credentials=JSON.parse(await readFile(credentialFile,'utf8'));
const auth='Basic '+Buffer.from(credentials.publicKey+':'+credentials.secretKey).toString('base64');
const previous=process.env.OW_SDK_OBSERVABILITY_AUTH;
process.env.OW_SDK_OBSERVABILITY_AUTH=auth;
const root=await mkdtemp(join(tmpdir(),'ow-sdk-observability-'));
let calls=0;
const model=createServer(async(req,res)=>{
 for await(const _chunk of req){};calls++;
 res.writeHead(200,{'content-type':'text/event-stream'});
 for(const frame of [
  {choices:[{index:0,delta:{role:'assistant',content:'Fixture answer.'},finish_reason:null}]},
  {choices:[{index:0,delta:{},finish_reason:'stop'}]},
  {choices:[],usage:{prompt_tokens:42,completion_tokens:7,total_tokens:49,prompt_tokens_details:{cached_tokens:12}}},
 ])res.write(`data: ${JSON.stringify(frame)}\n\n`);
 res.end('data: [DONE]\n\n');
});
await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
let harness;
try {
 harness=await Harness.start({executable,stateDir:join(root,'state'),sandbox:'off',recording:{database:{type:'sqlite',path:join(root,'traces.sqlite')},evaluations:true,evaluationSinks:[{name:'local-langfuse',url:endpoint.origin,authorizationEnv:'OW_SDK_OBSERVABILITY_AUTH'}]},telemetry:langfuseTelemetry(`${endpoint.origin}/api/public/otel`,'OW_SDK_OBSERVABILITY_AUTH')});
 await harness.setModelProvider({name:'ObservabilityFixture',api:'chat-completions',baseUrl:`http://127.0.0.1:${model.address().port}/v1`});
 const session=await harness.createAgent({name:'recording-demo',instructions:'Reply briefly.',tools:[],planning:false,memory:false,scheduling:false,defaultModel:{provider:'ObservabilityFixture',name:'fixture'}}).newSession();
 const result=await session.run('Private synthetic prompt for recording qualification.');assert.equal(result.status,'completed');assert.equal(calls,1);
 const turns=await harness.observability.listTurns({sessionId:session.id});
 const query={sessionId:session.id,turnId:turns.turns[0].turn_id};
 let timeline,summary;
 const deadline=Date.now()+15000;
 do {timeline=await harness.observability.timeline(query);summary=await harness.observability.summary({...query,through:timeline.page.through});if(summary.summary.usage.input.total===42&&timeline.page.steps.some(s=>s.step.operation==='agent.execute'))break;await delay(100);}while(Date.now()<deadline);
 assert.equal(summary.summary.usage.input.total,42);assert.equal(summary.summary.usage.output.total,7);assert.equal(summary.summary.usage.cache_read.total,12);
 const modelSpan=timeline.page.steps.find(r=>r.step.operation==='model.attempt').step;
 const scoreInput={id:`qualification-${query.turnId}`,name:'fixture-correctness',evaluator:{name:'fixture',version:'1',source:'code'},status:'completed',score:{type:'boolean',value:true},createdMs:Date.now()};
 const score=await harness.observability.recordEvaluation(query,scoreInput,modelSpan.span_id);
 assert.equal(score.duplicate,false);
 assert.equal((await harness.observability.recordEvaluation(query,scoreInput,modelSpan.span_id)).duplicate,true);
 const scoreDeadline=Date.now()+15000;
 while((await harness.observability.health()).evaluation_export.sent<1&&Date.now()<scoreDeadline)await delay(100);
 assert.equal((await harness.observability.health()).evaluation_export.sent,1);
 assert.equal(calls,1,'Evaluation export must not rerun the model');
 await harness.shutdown();harness=undefined;
 const scoreId=createHash('sha256').update(`sdk\0${scoreInput.id}`).digest('hex');
 const source=timeline.page.steps.map(s=>s.step);const traceIds=[...new Set(source.map(s=>s.trace_id))];
 const until=Date.now()+90000;let observations=[];
 do {
  observations=[];
  for(const traceId of traceIds){
   const qs=new URLSearchParams({traceId,limit:'1000',fields:'core,basic,usage,io,model,metadata',fromStartTime:new Date(Date.now()-120000).toISOString(),toStartTime:new Date().toISOString()});
   const response=await fetch(`${endpoint.origin}/api/public/v2/observations?${qs}`,{headers:{authorization:auth},signal:AbortSignal.timeout(10000)});
   assert.equal(response.status,200,'Langfuse observation query failed');observations.push(...(await response.json()).data);
  }
  if(source.every(s=>observations.some(o=>o.id===s.span_id)))break;
  await delay(1000);
 }while(Date.now()<until);
 const byId=new Map(observations.map(o=>[o.id,o]));
 for(const s of source){const o=byId.get(s.span_id);assert(o,`Missing exported span ${s.span_id}`);assert.equal(o.traceId,s.trace_id);assert.equal(o.parentObservationId??null,s.parent_span_id??null);assert(!o.input&&!o.output,'Metadata-only export leaked content');}
 const generations=source.filter(s=>s.operation==='model.attempt');assert.equal(generations.length,1);
 const usage=byId.get(generations[0].span_id).usageDetails;
 assert.equal((usage.input??0)+(usage.input_cached_tokens??0),42);assert.equal(usage.output,7);assert.equal(usage.total,49);
 assert.equal(observations.filter(o=>o.id===generations[0].span_id).length,1,'Duplicate model observation');
 let vendorScore;
 const scoreUntil=Date.now()+90000;
 do {
  const response=await fetch(`${endpoint.origin}/api/public/v3/scores?id=${scoreId}&fields=subject,details&limit=100`,{headers:{authorization:auth},signal:AbortSignal.timeout(10000)});
  assert.equal(response.status,200,'Langfuse scores query failed');
  vendorScore=(await response.json()).data.find(s=>s.id===scoreId);
  if(vendorScore)break;
  await delay(1000);
 }while(Date.now()<scoreUntil);
 assert(vendorScore,'Exported evaluation missing from Langfuse');
 assert.equal(vendorScore.subject.traceId,modelSpan.trace_id);assert.equal(vendorScore.subject.id,modelSpan.span_id);assert.equal(vendorScore.value,true);
 assert.equal(vendorScore.name,scoreInput.name);
 await writeFile(outputFile,JSON.stringify({version:1,qualifiedAt:new Date().toISOString(),calls,matchedSpans:source.length,evaluationRecord:score.result,evaluation:{id:scoreId,traceId:modelSpan.trace_id,spanId:modelSpan.span_id,name:scoreInput.name,value:1},usage:{input:42,output:7,cacheRead:12},timeline,summary,langfuse:{url:endpoint.origin,traceIds}},null,2)+'\n',{mode:0o600});
 console.log(JSON.stringify({ok:true,matchedSpans:source.length,input:42,output:7,cacheRead:12}));
} finally {
 await harness?.shutdown();model.closeAllConnections();await new Promise(resolve=>model.close(resolve));await rm(root,{recursive:true,force:true});
 if(previous===undefined)delete process.env.OW_SDK_OBSERVABILITY_AUTH;else process.env.OW_SDK_OBSERVABILITY_AUTH=previous;
}
