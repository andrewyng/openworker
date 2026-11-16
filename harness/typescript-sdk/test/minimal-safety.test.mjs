import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Harness,guard} from "../dist/index.js";

test("minimal native host rejects required safety and compliance instead of ignoring them",{skip:!process.env.OW_TS_MINIMAL_HOST,timeout:30000},async()=>{
  const root=await mkdtemp(join(tmpdir(),"ow-minimal-safety-"));
  const options={executable:process.env.OW_TS_MINIMAL_HOST,sandbox:"off",restart:{maxRestarts:0,delayMs:0}};
  let harness;
  try {
    harness=await Harness.start({...options,stateDir:join(root,"plain")});
    assert.equal(harness.capabilities.guardrails,false);
    assert.equal(harness.capabilities.compliance,false);
    const agent=harness.createAgent({name:"guide",instructions:"Hint only",tools:[],memory:false,planning:false,scheduling:false});
    await assert.rejects(agent.newSession({guardrails:{userInput:[guard.creditCard()]}}),/guardrails/);
    await assert.rejects(Harness.start({...options,stateDir:join(root,"required"),compliance:{retentionDays:30}}));
  } finally {await harness?.shutdown();await rm(root,{recursive:true,force:true});}
});
