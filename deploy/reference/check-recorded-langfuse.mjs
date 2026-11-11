// Compare an exported OpenShell service qualification with local Langfuse.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
const [file,url,credentialFile,output]=process.argv.slice(2);
const endpoint=new URL(url);assert(['127.0.0.1','localhost'].includes(endpoint.hostname));
const fixture=JSON.parse(await readFile(file,'utf8'));
const credentials=JSON.parse(await readFile(credentialFile,'utf8'));
const auth='Basic '+Buffer.from(credentials.publicKey+':'+credentials.secretKey).toString('base64');
const records=[fixture,...fixture.childTraces].flatMap(f=>f.timeline.page.steps.map(r=>r.step));
assert.equal(new Set(records.map(r=>r.span_id)).size,records.length,'Duplicate local span');
const ids=[...new Set(records.map(r=>r.trace_id))];
const start=new Date(Math.min(...records.map(r=>r.started_ms))-60000).toISOString();
let observations=[];const deadline=Date.now()+90000;
do {
 observations=[];
 for(const traceId of ids){const qs=new URLSearchParams({traceId,limit:'1000',fields:'core,basic,usage,io,model,metadata',fromStartTime:start,toStartTime:new Date().toISOString()});
 const response=await fetch(`${endpoint.origin}/api/public/v2/observations?${qs}`,{headers:{authorization:auth},signal:AbortSignal.timeout(10000)});assert.equal(response.status,200);observations.push(...(await response.json()).data);}
 if(records.every(r=>observations.some(o=>o.id===r.span_id)))break;await delay(1000);
}while(Date.now()<deadline);
const observed=new Map(observations.map(o=>[o.id,o]));
for(const r of records){const o=observed.get(r.span_id);assert(o,`Missing span ${r.span_id}`);assert.equal(o.traceId,r.trace_id);assert.equal(o.parentObservationId??null,r.parent_span_id??null);assert(!o.input&&!o.output,'Content leaked');
 if(r.operation==='model.attempt'){
  assert.equal(observations.filter(o=>o.id===r.span_id).length,1,'Duplicate model span');
  if(r.usage?.input!=null)assert.equal((o.usageDetails.input??0)+(o.usageDetails.input_cached_tokens??0),r.usage.input);
  if(r.usage?.output!=null)assert.equal(o.usageDetails.output,r.usage.output);
 }
}
const result={ok:true,qualifiedAt:new Date().toISOString(),matchedSpans:records.length,modelAttempts:records.filter(r=>r.operation==='model.attempt').length,toolCalls:records.filter(r=>r.operation==='tool.execute').length,visibleTurns:fixture.subtree.selection.runs.length,traceIds:ids,contentExported:false};
await writeFile(output,JSON.stringify(result,null,2)+'\n',{mode:0o600});console.log(JSON.stringify(result));
