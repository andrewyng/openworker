// Real managed host; no model/network side effects. PostgreSQL uses a unique schema.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { Harness } from "../dist/index.js";
for (const postgres of [false, true]) test(`cancel unanswered approval settles without another interruption (${postgres ? "postgres" : "sqlite"})`, {
  skip: !process.env.OW_TS_TEST_HOST || postgres && !process.env.OW_PG_TEST_URL,
  timeout: 180000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), "ow-cancel-approval-"));
  const executable = process.env.OW_TS_TEST_HOST;
  const storage = postgres ? {type:"postgres",connectionEnv:"OW_PG_TEST_URL",schema:"ow_cancel_"+randomUUID().replaceAll("-",""),loopbackPlaintext:true} : {type:"sqlite"};
  if (postgres) {
    const config = join(root,"migrate.json");
    await writeFile(config,JSON.stringify({state_dir:root,runner:executable,coworkers:[],sandbox:"off",storage:{kind:"postgres",connection_env:storage.connectionEnv,schema:storage.schema,loopback_plaintext:true}}));
    execFileSync(executable,["sdk-test-migrate",config],{stdio:"pipe"});
  }
  let h;
  t.after(async()=>{await h?.shutdown();await rm(root,{recursive:true,force:true});});
  h = await Harness.start({executable,stateDir:root,sandbox:"off",scripted:true,storage});
  let effects = 0;
  await h.registerTools({namespace:"fixture",tools:{action:{description:"Count a synthetic action.",inputSchema:{type:"object",properties:{},additionalProperties:false},execute:()=>{effects++;return {ok:true};}}}});
  const agent = h.createAgent({name:"worker",instructions:"Use action.",tools:["fixture.action"],approvals:{mode:"always"}});
  for (let n=0;n<3;n++) {
    const session = await agent.newSession();
    const run = await session.send('TEST_RESOURCE_GRANTS [{"name":"fixture__action","arguments":{}}]');
    assert.equal((await run.result()).status,"interrupted");
    await session.cancel();
    const result = await run.result();
    assert.equal(result.status,"cancelled");
    assert.deepEqual(result.interruptions,[]);
    assert.equal((await session.state()).pending.length,0);
    assert.equal(effects,0,"Cancellation before approval cannot execute the tool");
  }
});
