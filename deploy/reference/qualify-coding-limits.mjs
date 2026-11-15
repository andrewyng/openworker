// Explicit local acceptance after a completed build/follow-up. No task/tool retry.
import assert from 'node:assert/strict';
import {readFile,writeFile,appendFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {Harness} from '../../harness/typescript-sdk/dist/index.js';
const [directory,attempt='1',resume]=process.argv.slice(2);assert(!resume||['resume-budget','resume-results'].includes(resume),'Use resume-budget or resume-results to inspect original completed probes');assert(directory&&/^[1-9][0-9]?$/.test(attempt),'STATE [ATTEMPT] required');
const root=resolve(directory),state=join(root,attempt==='1'?'acceptance':`acceptance-${attempt}`);
const m=JSON.parse(await readFile(join(root,'service/kubernetes.json')));
assert(m.context.startsWith('kind-')&&/^ow-service-m4-coding[0-9]{8}$/.test(m.namespace),'Owned local fixture only');
let followup=JSON.parse(await readFile(join(state,'followup-result.json')));
if(followup.status!=='completed')followup=JSON.parse(await readFile(join(state,'recover-result.json')));
assert.equal(followup.status,'completed','Inspect/finish the original follow-up first');
const save=(name,v)=>writeFile(join(state,name),JSON.stringify(v,null,2)+'\n',{mode:0o600,flag:'wx'});
const h=await Harness.connect({url:m.url,namespace:'local',credentials:{getToken:async()=>String(await readFile(join(root,'service/input/admin-token'))).trim()}});
async function execute(session,label,text,options){
 await save(label+'-intent.json',{text,...options});
 const run=await session.send(text,options);await save(label+'-run.json',{id:run.id,submissionId:run.submissionId});
 const events=[];
 for await(const item of run.events()){const e=item.event??item;events.push(e);await appendFile(join(state,label+'-events.ndjson'),JSON.stringify(e)+'\n',{mode:0o600});}
 const result=await run.snapshot();await save(label+'-result.json',result);assert.equal(result.status,'completed');return {run,events,result};
}
try{
 const session=await h.resume(JSON.parse(await readFile(join(state,'session.json'))));
 let limited;
 if(resume){const r=JSON.parse(await readFile(join(state,'budget-run.json')));limited={run:await session.resumeRun(r.submissionId),events:String(await readFile(join(state,'budget-events.ndjson'))).trim().split('\n').map(l=>{const e=JSON.parse(l);return e.event??e}),result:JSON.parse(await readFile(join(state,'budget-result.json')))};}
 else limited=await execute(session,'budget','Create budget-proof.txt with the text "should not execute" using one write_file call. Do not use any other tool or modify another file.',{idempotencyKey:`budget-proof-${attempt}`,limits:{maxModelCalls:1,maxOutputTokens:2048,summaryTokens:512}});
 assert(limited.events.some(e=>e.type==='turn_budget_exceeded'&&e.data.reason==='model_call_limit'));
 assert(limited.events.some(e=>e.type==='tool_finished'&&e.data.result?.executed===false&&e.data.result?.code==='turn_limit_exceeded'));
 assert(limited.result.finalOutput?.length,'A meaningful final response is required');
 // The user message deliberately exercises a non-mutating malformed request;
 // correction is a new model decision, never an SDK retry of a tool action.
 let recovered;
 if(resume==='resume-results'){const r=JSON.parse(await readFile(join(state,'recovery-run.json')));recovered={run:await session.resumeRun(r.submissionId),events:String(await readFile(join(state,'recovery-events.ndjson'))).trim().split('\n').map(l=>{const e=JSON.parse(l);return e.event??e}),result:JSON.parse(await readFile(join(state,'recovery-result.json')))};}
 else recovered=await execute(session,'recovery','Run this non-mutating integration check. First make exactly one read_file call with empty arguments {} to exercise the diagnostic, then correct it by reading preview.json. Check with run_shell that budget-proof.txt does not exist and the existing HTTP /health still works. Do not create or edit any file. Finally start a harmless "sleep 30" with process_start (timeout_seconds 60), then explicitly stop that new handle with process_stop. Do not stop/restart the app server. Report actual results briefly.',{idempotencyKey:`recovery-proof-${attempt}`});
 assert(!recovered.events.some(e=>e.type==='turn_budget_exceeded'),'The one-turn override must not stick');
 assert(recovered.events.filter(e=>e.type==='model_attempt_started').length>2);
 // A model/provider may enforce the input schema before a malformed call.
 // Observe natural correction from the earlier recorded coding turn instead
 // of resubmitting this probe or forcing the model around schema enforcement.
 let validationSeen=recovered.events.some(e=>e.type==='tool_validation_failed');
 if(!validationSeen){
  const r=JSON.parse(await readFile(join(state,'recover-run.json')));
  const v=await h.json('GET',`/v1/sessions/${session.id}/observability/${r.runId}/timeline?limit=200`);
  validationSeen=v.page.steps.some(x=>x.step.operation==='tool.validation_failed');
  await save('natural-validation-recording.json',v);
 }
 assert(validationSeen,'A recorded validation event is required; deterministic runtime tests cover malformed calls independently');
 const recordings={};
 for(const [label,item] of [['budget',limited],['recovery',recovered]]){
  const base=`/v1/sessions/${session.id}/observability/${item.run.id}`;
  let timeline;
  for(let i=0;i<20;i++){
   timeline=await h.json('GET',base+'/timeline?limit=200');
   const operations=(timeline.page?.steps??[]).map(x=>x.step?.operation??x.operation);
   if(operations.includes(label==='budget'?'turn.budget_exceeded':'process.stopped'))break;
   await new Promise(r=>setTimeout(r,500));
  }
  const operations=(timeline.page?.steps??[]).map(x=>x.step.operation);
  if(label==='budget')assert(operations.includes('turn.budget_exceeded'));
  if(label==='recovery')for(const op of ['process.started','process.stopped'])assert(operations.includes(op));
  const summary=await h.json('GET',base+'/summary');await save(label+'-recording.json',{summary,timeline});
  recordings[label]=timeline;
 }
 console.log(JSON.stringify({passed:true,limitRefusedQueuedTool:true,finalSummary:true,nextTurnUsesDefaultBudget:true,invalidArgumentCorrected:true,recordingsSaved:true,runIds:{budget:limited.run.id,recovery:recovered.run.id}}));
}finally{h.disconnect();}
