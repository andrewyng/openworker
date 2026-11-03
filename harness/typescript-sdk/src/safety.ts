import { HarnessError } from "./types.js";
import type { Json, Model } from "./types.js";

export interface SafetyContext {
  evaluationId: string;
  callId: string | null;
  sessionId: string;
  turnId: string | null;
  phase: "userInput" | "modelInput" | "modelOutput" | "toolCall" | "toolResult" | "event";
  text: string;
  tool: string | null;
  arguments: Json;
  /** UTF-8 byte offsets. Use match.text, not JavaScript slice(start, end). */
  matches: { start: number; end: number; text: string }[];
  context: Json;
  signal: AbortSignal;
}
export type HookDecision = { action: "allow" } | {
  action: "block"; code: string; feedbackToModel?: string;
};
export type SafetyDecision = HookDecision | { action: "replace"; text: string };
export type SafetyHandler = (context: SafetyContext) => SafetyDecision | Promise<SafetyDecision>;
export type Replacement = (context: SafetyContext) => string | Promise<string>;
type HandlerRef = { name: string; version: string };
type Action = {type:"block"} | {type:"redact";replacement:string;keepLast:number} | {type:"callback";handler:HandlerRef};
export interface Guardrail {
  name: string; version: string; timeoutMs: number;
  detector: {type:"creditCard"|"secrets"} | {type:"regex";pattern:string;caseInsensitive:boolean} | {type:"model";model:{connection:string;model:string};instructions:string};
  action: Action;
  /** Application-only callback; normalizeSafety removes it from the wire. */
  callback?: SafetyHandler;
}
export interface SafetyHook {
  name:string; version:string; when:"beforeInput"|"beforeModel"|"beforeTool"|"afterTool"|"beforeOutput"|"onEvent";
  tools:string[]; handler:HandlerRef; timeoutMs:number;
  callback?:SafetyHandler;
}
export interface Guardrails {
  userInput?: Guardrail[];
  modelOutput?: Guardrail[];
  toolCalls?: {tools:string[];rules:Guardrail[]}[];
  toolResults?: {tools:string[];rules:Guardrail[]}[];
}
export interface SafetyOptions {
  guardrails?:Guardrails;
  hooks?:SafetyHook[];
  blockedOutputOptions?: {maxRetries:number};
}
interface RuleOptions {
  name?:string; version?:string; timeoutMs?:number;
  action?:"block"|"redact"|Replacement;
  replacement?:string; keepLast?:number;
}
function rule(name:string,detector:Guardrail["detector"],options:RuleOptions):Guardrail {
  const identity=options.name??name,version=options.version??"1";
  const result:Guardrail={name:identity,version,timeoutMs:options.timeoutMs??10000,detector,action:{type:"block"}};
  if (options.action==="redact") result.action={type:"redact",replacement:options.replacement??"[redacted]",keepLast:options.keepLast??0};
  if (typeof options.action==="function") {
    const callback=options.action;
    result.action={type:"callback",handler:{name:identity,version}};
    result.callback=async context=>({action:"replace",text:await callback(context)});
  }
  return result;
}
export const guard = {
  creditCard:(options:RuleOptions={})=>rule("builtin.credit-card",{type:"creditCard"},options),
  secrets:(options:RuleOptions={})=>rule("builtin.secrets",{type:"secrets"},options),
  regex:(options:RuleOptions&{name:string;pattern:RegExp|string})=>{
    const pattern=options.pattern;
    if (pattern instanceof RegExp && /[^gi]/.test(pattern.flags))
      throw new HarnessError("InvalidInput","Guardrail RegExp supports only g and i flags; use the documented Rust-compatible pattern syntax");
    return rule(options.name,{type:"regex",pattern:typeof pattern==="string"?pattern:pattern.source,caseInsensitive:pattern instanceof RegExp&&pattern.ignoreCase},options);
  },
  model:(options:RuleOptions&{name:string;model:Model;instructions:string})=>rule(options.name,{type:"model",model:{connection:options.model.provider,model:options.model.name},instructions:options.instructions},options),
};
interface HookOptions {name:string;version?:string;tools?:string[];timeoutMs?:number;run:(context:SafetyContext)=>HookDecision|Promise<HookDecision>}
function makeHook(when:SafetyHook["when"],options:HookOptions):SafetyHook {
  return {name:options.name,version:options.version??"1",when,tools:options.tools??[],handler:{name:options.name,version:options.version??"1"},timeoutMs:options.timeoutMs??10000,callback:options.run};
}
export const hook={
  beforeInput:(options:HookOptions)=>makeHook("beforeInput",options),
  beforeModel:(options:HookOptions)=>makeHook("beforeModel",options),
  beforeTool:(options:HookOptions)=>makeHook("beforeTool",options),
  afterTool:(options:HookOptions)=>makeHook("afterTool",options),
  beforeOutput:(options:HookOptions)=>makeHook("beforeOutput",options),
  onEvent:(options:Omit<HookOptions,"run">&{run:(context:SafetyContext)=>void|Promise<void>})=>makeHook("onEvent",{...options,run:async c=>{await options.run(c);return {action:"allow"};}}),
};

/** Compile callbacks to trusted registered references. Never JSON-serialize a function. */
export async function normalizeSafety(options:SafetyOptions,register:(ref:HandlerRef,handler:SafetyHandler)=>Promise<void>):Promise<Json|undefined> {
  if (!options.guardrails&&!options.hooks&&!options.blockedOutputOptions)return undefined;
  async function normalize<T extends Guardrail|SafetyHook>(definition:T):Promise<Json> {
    const {callback,...wire}=definition;
    if (callback) {
      const ref="handler" in definition?definition.handler:definition.action.type==="callback"?definition.action.handler:undefined;
      if (!ref)throw new HarnessError("InvalidInput","Safety callback requires a handler reference");
      await register(ref,callback);
    }
    return wire as unknown as Json;
  }
  const rules=options.guardrails??{};
  const groups=async(items:{tools:string[];rules:Guardrail[]}[]|undefined)=>Promise.all((items??[]).map(async g=>({tools:g.tools,rules:await Promise.all(g.rules.map(normalize))})));
  return {guardrails:{userInput:await Promise.all((rules.userInput??[]).map(normalize)),modelOutput:await Promise.all((rules.modelOutput??[]).map(normalize)),toolCalls:await groups(rules.toolCalls),toolResults:await groups(rules.toolResults)},hooks:await Promise.all((options.hooks??[]).map(normalize)),...(options.blockedOutputOptions?{blockedOutputOptions:{...options.blockedOutputOptions}}:{})};
}
