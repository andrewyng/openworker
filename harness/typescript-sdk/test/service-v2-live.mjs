import assert from "node:assert/strict";
import { Harness } from "../dist/index.js";
const service = await Harness.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: "test",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
try {
  const reference = JSON.parse(process.env.OW_SERVICE_REFERENCE);
  const gitReference=JSON.parse(process.env.OW_GIT_SERVICE_REFERENCE??"null");
  if(gitReference){
    const admin=await Harness.connect({url:process.env.OW_SERVICE_URL,namespace:"test",credentials:{tokenEnv:"OW_GIT_ADMIN_TOKEN"}});
    try{
      const profile=JSON.parse(process.env.OW_GIT_PROFILE);
      const registered=await admin.registerRuntimeProfile(profile,{idempotencyKey:"ts-git-register"});
      assert.equal(registered.definition.gitAccess,"fixture-git@1");
    }finally{admin.disconnect();}
    const git=await service.resume(gitReference);
    const run=await git.send('TEST_RESOURCE_GRANTS [{"name":"run_shell","arguments":{"command":"git -C sdk-checkout fetch -q origin && printf typescript-git-ok","yield_ms":10000}}]',{idempotencyKey:"typescript-git"});
    assert.equal((await run.result()).status,"completed");
    const events=await service.json("GET",`/v1/sessions/${git.id}/events/page?after=0`);
    assert.ok(events.events.some(e=>e.type==="tool_finished" && e.data.result.content?.some(c=>{try{const v=JSON.parse(c.text);return v.exit_code===0 && v.stdout==="typescript-git-ok";}catch{return false;}})));
    console.log("PASS TypeScript Git RuntimeProfile registration, original-session resume and real OpenShell fetch");
  }
  const handoff=JSON.parse(process.env.OW_HANDOFF_REFERENCE??"null");
  if(handoff){
    const lead=await service.resume(handoff.reference);const team=await lead.resumeTeam();
    const before=await team.submissions({limit:20});assert.equal(before[0].id,handoff.submissionId);assert.equal(before[0].status,"ready");assert.ok(before[0].integrations.some(i=>i.status==="applied"));
    const task=`TEST_RESOURCE_GRANTS ${JSON.stringify([
      {name:"run_shell",arguments:{command:`test "$(git rev-parse HEAD)" = '${handoff.commit}' && test "$(cat proof.txt)" = checkout-a && printf typescript-submission-ok`,yield_ms:10000}},
      {name:"team_report_verification",arguments:{submission_id:handoff.submissionId,report:"TypeScript-created verifier checked the original submitted commit and file."}}
    ])}`;
    const verifier=await team.spawn("typescript-submission-verifier","builder","typescript-submission",task,{source:{kind:"submission",id:handoff.submissionId}});
    const deadline=Date.now()+180000;
    while(true){const members=await team.members();const row=members.members.find(r=>r.instance.agent_id==="typescript-submission");assert.equal(row.error,null,JSON.stringify(row));if(row.last_outcome==="completed")break;assert.ok(Date.now()<deadline,JSON.stringify(row));await new Promise(r=>setTimeout(r,150));}
    const events=await service.json("GET",`/v1/sessions/${verifier.instance.session_id}/events/page?after=0`);
    assert.ok(events.events.some(e=>e.type==="tool_finished" && e.data.result.content?.some(c=>{try{const v=JSON.parse(c.text);return v.exit_code===0 && v.stdout==="typescript-submission-ok";}catch{return false;}})));
    const after=await team.submissions({limit:20});assert.equal(after[0].id,handoff.submissionId);assert.equal(after[0].reports.length,2);assert.ok(after[0].reports.some(r=>r.reporter.agent_id==="typescript-submission"));
    console.log("PASS TypeScript exact-submission spawn, distinct OpenShell checkout, real Git/file verification and attributed report");
  }
  const coder = await service.resume(reference);
  await assert.rejects(service.resume({...reference,agent:'verifier@1'}),/release_identity_mismatch/);
  const verifier = await service.agent("verifier");
  assert.equal(verifier.id, "verifier@1");
  await assert.rejects(
    service.registerRuntimeProfile({
      id: "bad@1",
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "shared",
    }),
    (e) => e.status === 403,
  );
  await assert.rejects(
    verifier.newSession({
      environmentId: coder.environmentId,
      project: "different",
      idempotencyKey: "wrong-project",
    }),
    (e) => e.status === 403,
  );
  const session = await verifier.newSession({
    environmentId: coder.environmentId,
    project: "game",
    idempotencyKey: "verify-create",
  });
  assert.notEqual(session.id, coder.id);
  assert.equal(session.environmentId, coder.environmentId);
  const retry = await verifier.newSession({
    environmentId: coder.environmentId,
    project: "game",
    idempotencyKey: "verify-create",
  });
  assert.equal(retry.id, session.id);
  const run = await session.send(
    'TEST_RESOURCE_GRANTS [{"name":"read_file","arguments":{"path":"shared-proof.txt"}}]',
    { idempotencyKey: "read" },
  );
  assert.equal((await run.result()).status, "completed");
  let output = "";
  for await (const e of run.events()) output += JSON.stringify(e.event);
  assert.ok(output.includes("coder output for verifier"));
  const snapshot = await session.snapshot();
  assert.ok(!JSON.stringify(snapshot.messages).includes("write_file"));
  assert.equal(snapshot.session.permissions.mode, "bypass");
  const before = await coder.snapshot();
  assert.ok(!JSON.stringify(before.messages).includes("read_file"));
  let operationId;
  try {
    await verifier.newSession({
      project: "isolated",
      idempotencyKey: "isolated-create",
      waitTimeoutMs: 1,
    });
  } catch (error) {
    assert.equal(error.code, "provisioning_pending");
    assert.ok(error.operationId);
    operationId = error.operationId;
  }
  const isolated = await verifier.newSession({
    project: "isolated",
    idempotencyKey: "isolated-create",
  });
  assert.notEqual(isolated.environmentId, coder.environmentId);
  if (operationId)
    assert.equal(
      (await service.operation(operationId)).receipt.sessionId,
      isolated.id,
    );
  const teamLead=await (await service.agent("coder@1")).newSession({environmentId:coder.environmentId,project:"game",idempotencyKey:"typescript-team-lead"});
  const team=await teamLead.createTeam({agentTypes:{builder:"coder@1"},delegation:{tools:["coding.read_file"],placements:["team","member","new"],profiles:{"small@1":{cpu:1,memoryMB:512}}}});
  const member=await team.spawn("typescript-member","builder","checker",'TEST_RESOURCE_GRANTS [{"name":"read_file","arguments":{"path":"shared-proof.txt"}}]',{sandbox:{mode:"member",agentId:"lead"}});
  const deadline=Date.now()+120000;
  while(true){
    const roster=await team.members();const row=roster.members.find(r=>r.instance.agent_id==="checker");
    assert.equal(row.error,null,JSON.stringify(row));
    if(row.last_outcome==="completed")break;
    assert.ok(Date.now()<deadline,JSON.stringify(row));
    await new Promise(resolve=>setTimeout(resolve,150));
  }
  const child=await service.json("GET",`/v1/sessions/${member.instance.session_id}`);
  assert.equal(child.session.environment.environment_id,coder.environmentId);
  const evidence=await service.json("GET",`/v1/sessions/${member.instance.session_id}/events/page?after=0`);
  assert.ok(evidence.events.some(e=>e.type==="tool_finished" && JSON.stringify(e).includes("coder output for verifier")));
  console.log("PASS TypeScript delegated placement configuration, member serialization and original shared sandbox tool execution");
  console.log(
    "PASS TypeScript shared verifier, independent histories, profile permissions, owner/project gates and idempotency",
  );
} finally {
  service.disconnect();
}
