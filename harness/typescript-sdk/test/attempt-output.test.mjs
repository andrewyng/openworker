import test from "node:test";
import assert from "node:assert/strict";
import { AttemptOutput, providerDiagnostic } from "../dist/index.js";
function item(id, type, data = {}, session = "s") {
  return { sessionId:session, agent:"guide", event:{event_id:id,session_id:session,turn_id:"t",type,data:{}}, summary:{type,attemptId:"a",...data} };
}
test("attempt projection replaces failed candidates and deduplicates replay",()=>{
  const view=new AttemptOutput();
  view.apply(item(1,"model_attempt_started",{attempt:1}));
  view.apply(item(2,"assistant_delta",{text:"partial"}));
  view.apply(item(3,"reasoning_delta",{text:"candidate",kind:"summary",blockIndex:0}));
  assert.equal(view.text,"partial");
  view.apply(item(4,"model_attempt_finished",{status:"failed"}));
  assert.equal(view.text,"");assert.equal(view.reasoning,"");
  view.apply(item(5,"model_attempt_started",{attemptId:"b",attempt:2}));
  const delta=item(6,"assistant_delta",{attemptId:"b",text:"accepted"});
  view.apply(delta);view.apply(delta);
  view.apply(item(7,"model_attempt_finished",{attemptId:"b",status:"completed"}));
  assert.equal(view.text,"accepted");
  view.interrupt();assert.equal(view.text,"accepted");
});
test("attempt projection scopes sessions and drops cancelled provisional output",()=>{
  const view=new AttemptOutput(2,8);
  view.apply(item(1,"assistant_delta",{text:"abcdefghijk"},"one"));
  view.apply(item(1,"assistant_delta",{text:"second"},"two"));
  assert.equal(view.text,"abcdefghsecond");
  view.interrupt();assert.equal(view.text,"");
  assert.throws(()=>new AttemptOutput(0));
});
test("diagnostics expose only safe structured values",()=>{
  assert.deepEqual(providerDiagnostic({category:"authentication",retryable:false,http_status:401,request_id:"req_fixture",api:"anthropic",message:"SECRET",body:"SECRET"}),
    {category:"authentication",retryable:false,httpStatus:401,requestId:"req_fixture",api:"anthropic"});
  assert.equal(providerDiagnostic({category:"arbitrary secret",retryable:true}),undefined);
  assert.equal(providerDiagnostic({category:"transport",retryable:true,request_id:"sk-secret"}).requestId,undefined);
});
