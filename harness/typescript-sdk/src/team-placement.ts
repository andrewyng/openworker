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
