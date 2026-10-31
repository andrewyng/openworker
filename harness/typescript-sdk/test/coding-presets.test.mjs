import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {codingAgent,sharedCodingTeam,codingRelease,codingServiceTeam,CODING_PRESET_VERSION} from "../dist/index.js";
test("canonical coding configuration is shared with Rust; factories return independent values",async()=>{
  const data=JSON.parse(await readFile(new URL("../../harness-sdk/src/agents/presets/coding-v1.json",import.meta.url),"utf8"));
  const a=codingAgent("solo");assert.equal(CODING_PRESET_VERSION,data.version);assert.equal(a.instructions,data.single.instructions);assert.deepEqual(a.tools,data.single.tools);
  assert.equal(a.approvals,undefined);assert.equal(a.defaultModel,undefined);assert.equal(a.subagents,undefined);
  a.tools.length=0;assert.ok(codingAgent("other").tools.length>0);
});
test("shared preset pins base and default builder worktree without implicit children",()=>{
  const p=sharedCodingTeam("game",{repository:"source",base:"a".repeat(40)},{builder:{defaultModel:{provider:"Local",name:"model"}}});
  assert.equal(p.builder.defaultModel.name,"model");assert.deepEqual(p.team.workspaceDefaults.agentTypes,["game-builder"]);assert.equal(p.verifier.tools.includes("coding.write_file"),false);
  assert.throws(()=>sharedCodingTeam("game",{repository:"source",base:"HEAD"}));
  assert.throws(()=>sharedCodingTeam("game",{repository:"../escape",base:"a".repeat(40)}));
  assert.throws(()=>sharedCodingTeam("game",{repository:"/absolute",base:"a".repeat(40)}));
  assert.throws(()=>sharedCodingTeam("1game",{repository:"source",base:"a".repeat(40)}));
});
test("service releases preserve models and require explicit profile compatibility",()=>{
  const profile={id:"shared@1",provider:"openshell",cpu:1,memoryMB:512,sharing:"shared"};
  const a=codingAgent("solo",{defaultModel:{provider:"Local",name:"chosen"}});
  const r=codingRelease(a,"1",profile);assert.deepEqual(r.defaultModel,{connection:"Local",model:"chosen"});
  assert.throws(()=>codingRelease({...a,approvals:{mode:"never"}},"1",profile));
  assert.throws(()=>codingRelease({...a,memory:true},"1",profile));
  const p=sharedCodingTeam("game",{repository:"source",base:"a".repeat(40)});const team=codingServiceTeam(p,"1");
  assert.deepEqual(team.agentTypes,{"game-builder":"game-builder@1","game-verifier":"game-verifier@1"});assert.deepEqual(team.workspaceDefaults,p.team.workspaceDefaults);
  const separated=codingRelease({...a,approvals:{mode:"auto",reviewerModel:{provider:"Reviewer",name:"review-model"}}},"1",{...profile,approvalMode:"auto-approve"});
  assert.deepEqual(separated.defaultModel,{connection:"Local",model:"chosen"});assert.deepEqual(separated.reviewerModel,{connection:"Reviewer",model:"review-model"});
});
