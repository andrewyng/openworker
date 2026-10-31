import type {AgentDefinition,Model,Approvals,TurnLimits,WorkspaceDefaults} from "./types.js";
import type {AgentTeamOptions} from "./index.js";
import type {AgentRelease,RuntimeProfile,ServiceAgentTeamOptions} from "./service.js";
import {codingPresets} from "./coding-presets.js";
export const CODING_PRESET_VERSION=codingPresets.version;
export interface CodingOptions {
  instructions?:string;
  tools?:string[];
  defaultModel?:Model;
  approvals?:Approvals;
  autoSkipQuestions?:boolean;
  limits?:TurnLimits;
}
function role(name:string,kind:"single"|"lead"|"builder"|"verifier",options:CodingOptions={}):AgentDefinition {
  if(!/^[a-zA-Z0-9_-]{1,64}$/.test(name))throw Error("Invalid coding agent name");
  return {name,instructions:codingPresets[kind].instructions,tools:[...codingPresets[kind].tools],planning:true,...structuredClone(options)};
}
/** No implicit children, model choice, credentials, publication or bypass. */
export function codingAgent(name:string,options:CodingOptions={}):AgentDefinition {return role(name,"single",options)}
export interface SharedCodingPreset {
  lead:AgentDefinition;builder:AgentDefinition;verifier:AgentDefinition;
  team:AgentTeamOptions;
}
export function sharedCodingTeam(prefix:string,workspace:{repository:string;base:string},options:{lead?:CodingOptions;builder?:CodingOptions;verifier?:CodingOptions}={}):SharedCodingPreset {
  if(!/^[a-zA-Z0-9_-]{1,48}$/.test(prefix))throw Error("Invalid coding team prefix");
  if(!/^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(workspace.base))throw Error("Worktree defaults require a pinned commit SHA");
  const p=workspace.repository;
  if(p!=="." && (!p || p.length>512 || p.includes("\\") || p.split("/").some(c=>!c || c==="." || c===".." || /[\x00-\x1f]/.test(c))))throw Error("Repository must be relative to the project");
  const lead=role(`${prefix}-lead`,"lead",options.lead),builder=role(`${prefix}-builder`,"builder",options.builder),verifier=role(`${prefix}-verifier`,"verifier",options.verifier);
  const workspaceDefaults:WorkspaceDefaults={...workspace,agentTypes:[builder.name]};
  return {lead,builder,verifier,team:{agentTypes:[builder.name,verifier.name],workspaceDefaults}};
}
/** Service policy belongs to the activated RuntimeProfile, not local SDK flags. */
export function codingRelease(agent:AgentDefinition,version:string,profile:RuntimeProfile):AgentRelease {
  const mode=agent.approvals?.mode??"always";
  const expected=mode==="never"?"bypass":mode==="auto"||mode==="reviewer"?"auto-approve":"ask";
  if(agent.servers || agent.toolPolicy || agent.runtimePolicy || agent.planning!==true || agent.memory || agent.memoryScope || agent.scheduling || agent.subagents?.length || mode==="only" || expected!==(profile.approvalMode??"ask") || (agent.autoSkipQuestions??false)!==(profile.autoSkipQuestions??false))throw Error("Service release must match the installed coding bundle and RuntimeProfile");
  if(!/^[a-zA-Z0-9_.-]{1,64}$/.test(version))throw Error("Invalid release version");
  return {id:`${agent.name}@${version}`,instructions:agent.instructions,tools:[...agent.tools],runtimeProfiles:[profile.id],defaultRuntimeProfile:profile.id,allowedPrincipals:profile.allowedPrincipals,
    defaultModel:agent.defaultModel?{connection:agent.defaultModel.provider,model:agent.defaultModel.name}:undefined,
    reviewerModel:agent.approvals?.mode==="auto"?{connection:agent.approvals.reviewerModel.provider,model:agent.approvals.reviewerModel.name}:undefined,limits:agent.limits};
}
export function codingServiceTeam(preset:SharedCodingPreset,version:string):ServiceAgentTeamOptions {
  return {...structuredClone(preset.team),agentTypes:{[preset.builder.name]:`${preset.builder.name}@${version}`,[preset.verifier.name]:`${preset.verifier.name}@${version}`}};
}
