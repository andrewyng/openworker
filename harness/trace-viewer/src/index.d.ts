export interface TraceAdapter {
  timeline(query: {sessionId:string;turnId:string;after?:number;through?:number;limit?:number}): Promise<unknown>;
  summary(query: {sessionId:string;turnId:string;through?:number}): Promise<unknown>;
  children?(query: {sessionId:string;turnId:string}): Promise<unknown>;
  subtreeSummary?(query: {sessionId:string;turnId:string}): Promise<unknown>;
  evaluations?(query: {sessionId:string;turnId:string;limit?:number}): Promise<unknown>;
}
export interface ChildLink {child_session_id:string;child_turn_id:string|null;agent_id:string;phase:string}
export function mountTurnTrace(root:HTMLElement, options:{adapter:TraceAdapter;sessionId:string;turnId:string;onChild?:(child:ChildLink)=>void}): {refresh():void;dispose():void};
export function mountObservability(root:HTMLElement, options:{request:(path:string,signal?:AbortSignal)=>Promise<any>;isAdmin?:boolean;initialRun?:{session_id:string;turn_id:string}|null;directory?:boolean;openSession?:boolean;children?:boolean}):()=>void;
