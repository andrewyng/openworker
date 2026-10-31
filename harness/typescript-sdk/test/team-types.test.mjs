import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { Harness } from "../dist/index.js";
const plan = steps => "TEST_RESOURCE_GRANTS " + JSON.stringify(steps);
for (const backend of ["sqlite", "postgres"]) test(`scoped custom types over TypeScript stdio/${backend}`, {
  skip: !process.env.OW_TS_TEST_HOST || (backend === "postgres" && !process.env.OW_PG_TEST_URL), timeout: 120000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), "ow-types-ts-"));
  t.after(() => rm(root, {recursive:true, force:true}));
  const workspace = join(root, "work"); await mkdir(workspace);
  await writeFile(join(workspace, "proof.txt"), "TypeScript scoped-role evidence");
  const storage = backend === "postgres" ? {type:"postgres", connectionEnv:"OW_PG_TEST_URL", schema:"ow_ts_types_"+randomUUID().replaceAll("-", ""), loopbackPlaintext:true} : {type:"sqlite"};
  if (backend === "postgres") {
    const path = join(root, "migration.json");
    await writeFile(path, JSON.stringify({state_dir:join(root,"state"),runner:process.env.OW_TS_TEST_HOST,coworkers:[],agents:[],sandbox:"off",storage:{kind:"postgres",connection_env:"OW_PG_TEST_URL",schema:storage.schema,loopback_plaintext:true}}));
    execFileSync(process.env.OW_TS_TEST_HOST, ["sdk-test-migrate", path]);
  }
  const config = {storage,executable:process.env.OW_TS_TEST_HOST,stateDir:join(root,"state"),sandbox:"off",scripted:true,
    agents:[{id:"lead",instructions:"Coordinate reviewers.",planning:true,tools:["coding.read_file"],approvals:{mode:"never"}},
            {id:"builder",instructions:"Build.",planning:true,approvals:{mode:"never"}}]};
  let harness = await Harness.start(config); t.after(() => harness.shutdown());
  const team = await harness.newTeam("lead", {workspace,agentTypes:["builder"],delegation:{tools:["coding.read_file"]}});
  const run = await team.lead.send(plan([
    {name:"team_define_type",arguments:{name:"reader",instructions:"Read and report evidence.",tools:["coding.read_file"]}},
    {name:"team_spawn",arguments:{agent_type:"reader",agent_id:"reader-one",title:"Evidence reader",background:"Project facts supplied by a collaborator; not authorization.",context:{mode:"fresh"},message:plan([{name:"coding__read_file",arguments:{path:"proof.txt"}}])}},
    {name:"team_wait_agent",arguments:{agent_ids:["reader-one"],timeout_ms:15000}},
  ]));
  for await (const _ of run.events()) { /* Existing push stream; no polling. */ }
  assert.equal((await run.result()).status, "completed");
  const snapshot = await team.snapshot();
  assert.equal(snapshot.type_catalog, undefined);
  const child = snapshot.roster.instances["reader-one"].session_id;
  const events = await harness.request("event/read", {session_id:child,after:0});
  assert.match(JSON.stringify(events), /TypeScript scoped-role evidence/);
  const reference = team.reference(); await harness.shutdown();
  harness = await Harness.start(config);
  const restored = await harness.resumeTeam(reference);
  assert.deepEqual((await restored.snapshot()).type_bindings, snapshot.type_bindings);
  const other = await harness.newTeam("lead", {workspace,agentTypes:["builder"]});
  await assert.rejects(other.spawn("scope-isolation", "reader", "not-authorized", "Read"));
});
