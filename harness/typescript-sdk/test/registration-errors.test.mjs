// Real managed host/catalog; no provider credentials or model calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Harness} from '../dist/index.js';

test('registration conflict diagnostics survive managed-host restart', {
  skip: !process.env.OW_TS_TEST_HOST, timeout: 90000,
}, async t => {
  const stateDir = await mkdtemp(join(tmpdir(), 'ow-definition-conflict-'));
  t.after(() => rm(stateDir, {recursive:true, force:true}));
  const definition = {name:'guide-v1', instructions:'PRIVATE_ORIGINAL_PROMPT',tools:[],planning:false,memory:false,scheduling:false};
  for (let pass = 0; pass < 2; pass++) {
    const h = await Harness.start({stateDir,executable:process.env.OW_TS_TEST_HOST,sandbox:'off'});
    try {
      await h.createAgent(definition).ready();
      await h.createAgent(definition).ready();
      await assert.rejects(h.createAgent({...definition,instructions:'PRIVATE_NEW_PROMPT',approvals:{mode:'never'}}).ready(), e => {
        assert.equal(e.code,'Remote');
        assert.deepEqual(e.context,{reason:'agent_definition_conflict',agentId:'guide-v1',conflictingFields:['instructions','approvals']});
        assert.match(e.message,/use a new agent name/);
        assert.ok(!JSON.stringify(e).includes('PRIVATE_'));
        return true;
      });
      await h.createAgent(definition).ready();
    } finally { await h.shutdown(); }
  }
});
