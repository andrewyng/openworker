import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";
const plan = (steps) => `TEST_RESOURCE_GRANTS ${JSON.stringify(steps)}`;
const call = (name, arguments_) => ({ name, arguments: arguments_ });
async function history(h, s) { return h.request("history/read", { session_id: s.id, after: 0 }); }
async function output(h, s) {
  const item = (await history(h,s)).items.map(v=>v.item).reverse().find(v=>v.type==="function_call_output");
  const result=JSON.parse(item.output); assert.notEqual(result.isError,true,JSON.stringify(result));
  return JSON.parse(result.content[0].text);
}
async function until(fn) {
  for(let i=0;i<300;i++) { if(await fn()) return; await new Promise(r=>setTimeout(r,50)); }
  assert.fail("Timed out waiting for process lifecycle");
}
test("coding files, question policy and Linux process recovery", {
  timeout:180000,
}, async(t)=> {
  const root=await mkdtemp(join(tmpdir(),"ow-coding-ts-")); const work=join(root,"work"); await mkdir(work);
  const h=await Harness.start({executable:process.env.OW_TS_TEST_HOST,stateDir:join(root,"state"),sandbox:"off",scripted:true});
  t.after(async()=>{await h.shutdown();await rm(root,{recursive:true,force:true});});
  const definition={name:"coder",instructions:"Code.",autoSkipQuestions:true,approvals:{mode:"never"},tools:["coding.write_file","coding.process_start","coding.process_status","coding.process_stop"]};
  const a=h.createAgent(definition); const s=await a.newSession({workspace:work});
  const question=call("ask_user",{question:"Which color?",options:["blue"]});
  const run=await s.send(plan([question,call("coding__write_file",{path:"proof.txt",content:"built"})]));
  assert.equal((await run.result()).status,"completed");
  assert.equal(await readFile(join(work,"proof.txt"),"utf8"),"built");
  assert.match(JSON.stringify(await history(h,s)),/No human answer is available/);
  const ask=await a.newSession({workspace:work,autoSkipQuestions:false});
  const q=await ask.send(plan([question])); const qr=await q.result(); assert.equal(qr.status,"interrupted");
  assert.equal(qr.interruptions[0].kind,"question"); await ask.cancel();
  // Question skipping never grants a tool approval.
  const guarded=h.createAgent({...definition,name:"guarded",approvals:{mode:"always"}});
  const gs=await guarded.newSession({workspace:work});
  const gr=await gs.send(plan([question,call("coding__write_file",{path:"denied.txt",content:"no"})]));
  const r=await gr.result(); assert.equal(r.status,"interrupted"); assert.equal(r.interruptions[0].kind,"approval");
  await gs.send({type:"approval",interactionId:r.interruptions[0].id,decision:"deny"});
  assert.equal((await gr.result()).status,"completed");
  await assert.rejects(readFile(join(work,"denied.txt")));
  if(process.platform!=="linux") {
    const unsupported=await s.send(plan([call("coding__process_start",{command:"echo no"})]));
    assert.equal((await unsupported.result()).status,"completed");
    assert.match(JSON.stringify(await history(h,s)),/Managed coding processes currently require Linux/);
    return;
  }
  const start=await s.send(plan([call("coding__process_start",{command:"echo $$ > preview.pid; printf started >> launches; exec sleep 120"})]));
  assert.equal((await start.result()).status,"completed"); const id=(await output(h,s)).process_id;
  const status=await s.send(plan([call("coding__process_status",{process_id:id})]));
  assert.equal((await status.result()).status,"completed"); assert.equal((await output(h,s)).status,"running");
  const pid=Number((await readFile(join(work,"preview.pid"),"utf8")).trim());
  const old=h.pid; process.kill(old,"SIGKILL");
  await until(()=>h.state==="ready"&&h.pid!==old);
  await until(()=>{try {process.kill(pid,0);return false;}catch(e){if(e.code==="ESRCH")return true;throw e;}});
  const recovered=await a.resumeSession(s.reference());
  assert.equal((await recovered.snapshot()).session.permissions.auto_skip_questions,true);
  const stale=await recovered.send(plan([call("coding__process_status",{process_id:id})]));
  assert.equal((await stale.result()).status,"completed");
  assert.match(JSON.stringify(await history(h,recovered)),/Unknown process/);
  assert.equal(await readFile(join(work,"launches"),"utf8"),"started");
});
