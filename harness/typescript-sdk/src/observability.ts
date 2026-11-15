/** Optional trace queries. This module neither starts a server nor installs an exporter. */
import { HarnessError, type Telemetry } from "./types.js";
export interface TraceQuery { sessionId:string; turnId:string; after?:number; through?:number; limit?:number }
export interface TraceStep {
  version:number; deployment:string; role:string; trace_id:string; span_id:string;
  parent_trace_id:string|null; parent_span_id:string|null; session_hash:string|null; turn_hash?:string|null;
  operation:string; outcome:string; started_ms:number; ended_ms:number; duration_us:number|null;
  first_output_us:number|null; attempt:number|null; model:string|null; tool:string|null; purpose:string|null;
  output_bytes:number|null; usage:{input:number|null;output:number|null;cache_read:number|null;cache_write:number|null;reasoning:number|null}|null;
  links?:{trace_id:string;span_id:string}[];
  sandbox?:string|null;enforcement?:string|null;policy_digest?:string|null;
}
export interface Timeline {
  run:{session_id:string;turn_id:string};
  page:{steps:{cursor:number;step:TraceStep}[];through:number;next:number|null};
  coverage:{guarantee:"best_effort";order:"recording_cursor";snapshot_across_requests:false};
}
export interface Measure {total:number|null;known:number;unknown:number;overflow:boolean}
export interface Usage {input:Measure;output:Measure;cache_read:Measure;cache_write:Measure;reasoning:Measure}
export interface TraceGroup {
 name:string|null;purpose:string|null;calls:number;outcomes:Record<string,number>;
 accumulated_us:Measure;active_union_us:number|null;first_output_us:Measure;returned_bytes:Measure;usage:Usage|null;
}
export interface TraceCoverage {
 guarantee:string;scanned_steps:number;scan_truncated:boolean;group_truncated:boolean;
 expected_segments:number|null;recorded_segments:number;all_visible_segments_closed:boolean;pending_waits:number|null;
 requests_without_completion:number;completions_without_start:number;unknown_timing_steps:number;unknown_outcome_steps:number;
 clock_or_range_anomaly:boolean;hierarchy_incomplete:boolean;
}
export interface Summary {
  run:{session_id:string;turn_id:string};
  summary:{projection_version:number;through:number;coverage:TraceCoverage;wall_elapsed_us:number|null;observed_window_us:number|null;active_segments_accumulated_us:Measure;model_accumulated_us:Measure;tool_accumulated_us:Measure;exclusive_observed_us:Record<string,number>;usage:Usage;models:TraceGroup[];tools:TraceGroup[]};
}
export interface TurnPage {turns:{session_id:string;turn_id:string;status:string|null;agent:string;name:string}[];next_cursor:number|null;through:number}
export interface ChildLink {
 origin:{parent_session_id:string;parent_turn_id:string;parent_call_id:string|null};
 child_session_id:string;child_turn_id:string|null;agent_id:string;phase:string;
}
export interface SubtreeSummary {selection:{runs:{session_id:string;turn_id:string}[];selection_truncated:boolean;scope:"root_and_visible_spawned_initial_turns"};summary:Summary["summary"]}
export interface Children {children:ChildLink[];coverage:string}
export interface ObservabilityAdapter {
  subtreeSummary?(query:TraceQuery):Promise<SubtreeSummary>;
  children?(query:TraceQuery):Promise<Children>;

  timeline(query:TraceQuery):Promise<Timeline>;
  summary(query:TraceQuery):Promise<Summary>;
}
function checked(q:TraceQuery):void {
  if (![q.sessionId,q.turnId].every(s=>typeof s==="string"&&/^[a-zA-Z0-9_-]{1,80}$/.test(s)) ||
      ![q.after??0,q.through??0,q.limit??100].every(n=>Number.isSafeInteger(n)&&n>=0) ||
      (q.limit??100)<1 || (q.limit??100)>200 || (q.through!==undefined&&q.through<(q.after??0)))
    throw new HarnessError("InvalidInput","Invalid trace scope or page bounds");
}
function wire(q:TraceQuery):Record<string,unknown> {
  checked(q);
  return {session_id:q.sessionId,turn_id:q.turnId,after:q.after??0,through:q.through??null,limit:q.limit??100};
}
export class Observability implements ObservabilityAdapter {
  constructor(private readonly request:(method:string,args:Record<string,unknown>)=>Promise<unknown>){}
  async flush(timeoutMs=5000):Promise<Record<string,unknown>> {if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>5000)throw new HarnessError("InvalidInput","Flush budget must be 1..5000ms");return await this.request("observability/flush",{timeout_ms:timeoutMs}) as Record<string,unknown>;}
  async health():Promise<Record<string,unknown>> {return await this.request("observability/health",{}) as Record<string,unknown>;}
  async evidence(q:{sessionId:string;turnId:string;afterEventId?:number}):Promise<Record<string,unknown>> {
    checked({sessionId:q.sessionId,turnId:q.turnId});
    return await this.request("observability/evidence",{session_id:q.sessionId,turn_id:q.turnId,after_event_id:q.afterEventId??0}) as Record<string,unknown>;
  }
  async recordEvaluation(q:TraceQuery,input:EvaluationInput,spanId?:string):Promise<{result:Evaluation;duplicate:boolean}> {
    checked(q);
    return await this.request("observability/evaluation-record",{session_id:q.sessionId,turn_id:q.turnId,span_id:spanId??null,result:evaluationWire(input)}) as {result:Evaluation;duplicate:boolean};
  }
  async evaluations(q:TraceQuery):Promise<EvaluationPage> {
    return await this.request("observability/evaluation-list",wire({...q,limit:q.limit??25})) as EvaluationPage;
  }
  async listTurns(q:{sessionId:string;after?:number;limit?:number}):Promise<TurnPage> {
    if(!Number.isSafeInteger(q.after??0)||(q.after??0)<0||!Number.isSafeInteger(q.limit??25)||(q.limit??25)<1||(q.limit??25)>100)throw new HarnessError("InvalidInput","Invalid turn page");
    return await this.request("observability/turns",{session_id:q.sessionId,after:q.after??0,limit:q.limit??25}) as TurnPage;
  }
  async subtreeSummary(q:TraceQuery):Promise<SubtreeSummary>{return await this.request("observability/subtree-summary",wire(q)) as SubtreeSummary;}
  async children(q:TraceQuery):Promise<Children>{return await this.request("observability/children",wire(q)) as Children;}
  async timeline(q:TraceQuery):Promise<Timeline>{return await this.request("observability/timeline",wire(q)) as Timeline;}
  async summary(q:TraceQuery):Promise<Summary>{return await this.request("observability/summary",wire(q)) as Summary;}
}
/** Authenticated service APIs enforce access on every read, including descendants. */
export class ServiceObservability implements ObservabilityAdapter {
  constructor(private readonly get:(path:string)=>Promise<unknown>){}
  async health():Promise<Record<string,unknown>> {return await this.get("/v1/observability/health") as Record<string,unknown>;}
  async search(q:{q?:string;agent?:string;limit?:number;after?:string}={}):Promise<{runs:Record<string,unknown>[];next_cursor:string|null;as_of_us:number}> {
    const query=new URLSearchParams();for(const [k,v] of Object.entries(q))if(v!==undefined)query.set(k,String(v));
    return await this.get(`/v1/observability/runs?${query}`) as {runs:Record<string,unknown>[];next_cursor:string|null;as_of_us:number};
  }
  private path(q:TraceQuery,kind:string):string {
    checked(q);const args=new URLSearchParams();
    if(kind!=="children"&&q.through!==undefined)args.set("through",String(q.through));
    if(kind==="timeline"){if(q.after!==undefined)args.set("after",String(q.after));if(q.limit!==undefined)args.set("limit",String(q.limit));}
    return `/v1/sessions/${encodeURIComponent(q.sessionId)}/observability/${encodeURIComponent(q.turnId)}/${kind}${args.size ? `?${args}` : ""}`;
  }
  async subtreeSummary(q:TraceQuery):Promise<SubtreeSummary>{return await this.get(this.path(q,"subtree-summary")) as SubtreeSummary;}
  async children(q:TraceQuery):Promise<Children>{return await this.get(this.path(q,"children")) as Children;}
  async timeline(q:TraceQuery):Promise<Timeline>{return await this.get(this.path(q,"timeline")) as Timeline;}
  async summary(q:TraceQuery):Promise<Summary>{return await this.get(this.path(q,"summary")) as Summary;}
}
/** Direct Langfuse traces; auth is read in the trusted harness process. */
export function langfuseTelemetry(endpoint:string, authorizationEnv:string):Telemetry {
  return {endpoint,signals:["traces"],acknowledgement:"langfuse_v4",headers:[{name:"Authorization",value_env:authorizationEnv},{name:"x-langfuse-ingestion-version",value:"4"}],content:{capture:"metadata"}};
}

export interface EvaluationInput {
 id:string;name:string;evaluator:{name:string;version:string;source:"human"|"code"|"model";model?:string;usage?:TraceStep["usage"]};
 status:"completed"|"skipped"|"error";
 score?:{type:"numeric";value:number}|{type:"boolean";value:boolean}|{type:"categorical"|"text";value:string};
 createdMs:number;explanation?:string;datasetCase?:string;experiment?:string;supersedes?:string;
}
export interface Evaluation {
 version:1;deployment:string;
 target:{session_hash:string;turn_hash:string;trace_id:string|null;span_id:string|null};
 result:{id:string;name:string;evaluator:EvaluationInput["evaluator"];status:EvaluationInput["status"];score:EvaluationInput["score"]|null;created_ms:number;explanation:string|null;dataset_case:string|null;experiment:string|null;supersedes:string|null};
}
export interface EvaluationPage {results:{cursor:number;evaluation:Evaluation}[];next:number|null}
export function evaluationWire(input:EvaluationInput):Record<string,unknown> {
 return {id:input.id,name:input.name,evaluator:input.evaluator,status:input.status,score:input.score??null,created_ms:input.createdMs,explanation:input.explanation??null,dataset_case:input.datasetCase??null,experiment:input.experiment??null,supersedes:input.supersedes??null};
}
