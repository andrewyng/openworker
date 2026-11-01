import type {SandboxChoice} from "./types.js";
// Serialization only. Rust validates ceilings, type envelopes and provider policy.
export function sandboxWire(s:SandboxChoice|undefined):unknown {
  if(!s)return undefined;
  const allowed=s.mode==="team"?["mode"]:s.mode==="member"?["mode","agentId"]:["mode","profile","files","network","credentials","cpu","memoryMB"];
  if(Object.keys(s).some(k=>!allowed.includes(k)))throw Error("Unsupported sandbox setting");
  if(s.mode==="team")return {mode:"team"};
  if(s.mode==="member")return {mode:"member",agent_id:s.agentId};
  if(s.mode==="new")return {mode:"new",profile:s.profile,files:s.files===undefined?[]:s.files,network:s.network===undefined?[]:s.network,credentials:s.credentials===undefined?[]:s.credentials,cpu:s.cpu===undefined?1:s.cpu,memory_mb:s.memoryMB===undefined?512:s.memoryMB};
  throw Error("Invalid sandbox selection");
}

export interface ServiceTeamDelegation {
  tools: string[];
  models?: {provider:string;name:string}[];
  placements?: ("team"|"member"|"new")[];
  /** Immutable activated RuntimeProfile IDs. These are named grants, never
   * secret values; unsupported provider grants reject in the service. */
  profiles?: Record<string, {
    files?:string[];network?:string[];credentials?:string[];
    cpu:number;memoryMB:number;
  }>;
}
export function serviceDelegationWire(d:ServiceTeamDelegation):unknown {
  if(Object.keys(d).some(k=>!["tools","models","placements","profiles"].includes(k)))throw Error("Unsupported team delegation setting");
  if(d.models!==undefined && !Array.isArray(d.models))throw Error("Invalid delegated models");
  if(d.placements!==undefined && !Array.isArray(d.placements))throw Error("Invalid delegated placements");
  if(d.profiles!==undefined && (d.profiles===null || typeof d.profiles!=="object" || Array.isArray(d.profiles)))throw Error("Invalid delegated profiles");
  const profiles=Object.fromEntries(Object.entries(d.profiles??{}).map(([id,p])=>{
    if(Object.keys(p).some(k=>!["files","network","credentials","cpu","memoryMB"].includes(k)))throw Error("Unsupported delegated profile setting");
    return [id,{files:p.files===undefined?[]:p.files,network:p.network===undefined?[]:p.network,credentials:p.credentials===undefined?[]:p.credentials,cpu:p.cpu,memory_mb:p.memoryMB}];
  }));
  return {tools:d.tools,models:(d.models??[]).map(m=>({connection:m.provider,model:m.name})),placements:d.placements===undefined?["team"]:d.placements,profiles};
}
