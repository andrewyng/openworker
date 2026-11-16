import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp,mkdir,writeFile,readFile,rm,copyFile,realpath} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {execFileSync} from "node:child_process";
import {Harness,codingAgent,sharedCodingTeam} from "../dist/index.js";
const plan=steps=>"TEST_RESOURCE_GRANTS "+JSON.stringify(steps);
for(const backend of ["sqlite","postgres"])test(`TypeScript coding presets over private stdio/${backend}`,{skip:!process.env.OW_TS_TEST_HOST||(backend==="postgres"&&!process.env.OW_PG_TEST_URL),timeout:120000},async t=>{
  const temp=await mkdtemp(join(tmpdir(),"ow-coding-preset-ts-"));const root=await realpath(temp);let h,resumedHost;t.after(async()=>{await resumedHost?.shutdown();await h?.shutdown();await rm(root,{recursive:true,force:true});});
  const executable=join(root,"test-host");await copyFile(process.env.OW_TS_TEST_HOST,executable);
  const workspace=join(root,"work"),source=join(workspace,"source");await mkdir(source,{recursive:true});await writeFile(join(source,"proof.txt"),"base");
  const git=args=>execFileSync("/usr/bin/git",args,{cwd:source,encoding:"utf8"}).trim();
  git(["init","--quiet"]);git(["add","proof.txt"]);git(["-c","user.name=Fixture","-c","user.email=f@example.invalid","commit","--quiet","-m","base"]);const base=git(["rev-parse","HEAD"]);
  const storage=backend==="postgres"?{type:"postgres",connectionEnv:"OW_PG_TEST_URL",schema:"ow_ts_preset_"+randomUUID().replaceAll("-",""),loopbackPlaintext:true}:{type:"sqlite"};
  if(backend==="postgres"){
    const file=join(root,"migration.json");await writeFile(file,JSON.stringify({state_dir:join(root,"state"),runner:executable,coworkers:[],agents:[],sandbox:"off",storage:{kind:"postgres",connection_env:"OW_PG_TEST_URL",schema:storage.schema,loopback_plaintext:true}}));execFileSync(executable,["sdk-test-migrate",file]);
  }
  h=await Harness.start({executable,stateDir:join(root,"state"),sandbox:"off",scripted:true,storage});
  const solo=h.createAgent(codingAgent("solo",{approvals:{mode:"never"}}));const s=await solo.newSession({workspace});
  let run=await s.send(plan([{name:"coding__write_file",arguments:{path:"index.html",content:"<button>Play</button>"}}]));let events=[];for await(const e of run.events())events.push(e);assert.equal((await run.result()).status,"completed");assert.ok(events.length>0);assert.equal(await readFile(join(workspace,"index.html"),"utf8"),"<button>Play</button>");
  const p=sharedCodingTeam("game",{repository:"source",base},{lead:{approvals:{mode:"never"}},builder:{approvals:{mode:"never"}},verifier:{approvals:{mode:"never"}}});
  await Promise.all([p.lead,p.builder,p.verifier].map(a=>h.createAgent(a).ready()));
  const team=await h.newTeam(p.lead.name,{workspace,...p.team});assert.equal(Object.keys((await team.snapshot()).roster.instances).length,1);
  const child=await team.spawn("build","game-builder","ui",plan([{name:"coding__write_file",arguments:{path:"proof.txt",content:"ts builder"}}]));assert.equal(child.workspace.mode,"worktree");assert.equal(child.workspace.base,base);
  run=await team.lead.send(plan([{name:"team_wait_agent",arguments:{agent_ids:["ui"],timeout_ms:20000}}]));for await(const _ of run.events()){}assert.equal((await run.result()).status,"completed");
  assert.equal(await readFile(join(workspace,child.workspace.path,"proof.txt"),"utf8"),"ts builder");assert.equal(await readFile(join(source,"proof.txt"),"utf8"),"base");
  const reference=team.reference();const before=await team.snapshot();await h.shutdown();
  resumedHost=await Harness.start({executable,stateDir:join(root,"state"),sandbox:"off",scripted:true,storage});
  await Promise.all([p.lead,p.builder,p.verifier].map(a=>resumedHost.createAgent(a).ready()));
  const resumed=await resumedHost.resumeTeam(reference);assert.deepEqual((await resumed.snapshot()).roster,before.roster);
});
