/** Optional explicit export helpers. No background worker or network at import. */
import {createHash} from 'node:crypto';
import type {Evaluation} from './observability.js';
export interface ScoreExportOptions {url:string;authorizationEnv:string;includeExplanation?:boolean}
export function langfuseScore(value:Evaluation,includeExplanation=false):Record<string,unknown> {
 if(!value.target.trace_id)throw Error('Select a recorded span before exporting a Langfuse score');
 if(value.result.status!=='completed'||!value.result.score)throw Error('Skipped/error evaluations are not quality scores');
 const s=value.result.score;
 return {id:createHash('sha256').update(`${value.deployment}\0${value.result.id}`).digest('hex'),name:value.result.name,traceId:value.target.trace_id,observationId:value.target.span_id??undefined,dataType:s.type.toUpperCase(),value:s.type==='boolean'?(s.value?1:0):s.value,timestamp:new Date(value.result.created_ms).toISOString(),...(includeExplanation&&value.result.explanation?{comment:value.result.explanation}:{}),metadata:{'openworker.evaluation.id':value.result.id,evaluator:value.result.evaluator.name,evaluatorVersion:value.result.evaluator.version,evaluatorSource:value.result.evaluator.source,evaluatorModel:value.result.evaluator.model,evaluatorUsage:value.result.evaluator.usage,...(value.result.supersedes?{supersedes:value.result.supersedes}:{})}};
}
/** Sends one stable score. Does not retry unknown outcomes or re-export a trace. */
export async function exportLangfuseScore(value:Evaluation,options:ScoreExportOptions):Promise<{id:string}> {
 const url=new URL(options.url);
 if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||(url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname))))throw Error('Use a HTTPS origin or explicit loopback Langfuse origin');
 if(!/^[A-Z_][A-Z0-9_]{0,99}$/.test(options.authorizationEnv))throw Error('Invalid export credential reference');
 const auth=process.env[options.authorizationEnv];if(!auth)throw Error('Export credential unavailable');
 const body=langfuseScore(value,options.includeExplanation);let response;
 try {response=await fetch(new URL('/api/public/scores',url),{method:'POST',headers:{Authorization:auth,'Content-Type':'application/json'},body:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(3000)});}
 catch {throw Error('Score export outcome unknown; reconcile the stable score ID before retrying');}
 if(response.status!==200){await response.body?.cancel();throw Error(`Score export not confirmed (HTTP ${response.status}); no automatic retry`);}
 const reader=response.body?.getReader();if(!reader)throw Error('Score export acknowledgement missing');
 let bytes=0;const chunks:Uint8Array[]=[];
 for(;;){const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>65536){await reader.cancel();throw Error('Score acknowledgement too large; outcome unknown');}chunks.push(value);}
 let ack;try{ack=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw Error('Invalid score acknowledgement; outcome unknown');}
 if(ack?.id!==body.id)throw Error('Score identity mismatch; outcome unknown');
 return {id:String(body.id)};
}
/** Caller supplies authorized projections. Content is absent unless explicitly supplied. */
export function evidenceJsonl(input:{selection:{sessionId:string;turnId:string};watermark:number;timeline:unknown;summary:unknown;evaluations?:Evaluation[];content?:unknown}):string {
 const records=[{kind:'timeline',value:input.timeline},{kind:'summary',value:input.summary},...(input.evaluations??[]).map(value=>({kind:'evaluation',value})),...(input.content===undefined?[]:[{kind:'selected_content',value:input.content}])];
 const lines=records.map(r=>JSON.stringify(r)).join('\n')+'\n';
 if(Buffer.byteLength(lines)>8*1024*1024)throw Error('Evidence export exceeds 8 MiB; export smaller scoped pages');
 return JSON.stringify({kind:'manifest',version:1,selection:input.selection,watermark:input.watermark,contentIncluded:input.content!==undefined,coverage:'selected_pages_only',guarantee:'best_effort_traces_not_an_audit_archive',sha256:createHash('sha256').update(lines).digest('hex')})+'\n'+lines;
}
