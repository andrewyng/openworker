import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {Harness} from '../dist/index.js';
const executable=process.env.OW_TS_TEST_HOST;
assert.ok(executable,'Set OW_TS_TEST_HOST to a runtime with recording,test-support');
test('TypeScript managed recording persists and is queryable after reconnect',{timeout:30000},async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'ow-ts-observation-')));t.after(()=>rm(root,{recursive:true,force:true}));
 await mkdir(join(root,'project'));
 const options={executable,stateDir:join(root,'state'),sandbox:'off',scripted:true,recording:{database:{type:'sqlite',path:join(root,'traces.sqlite')}},agents:[{id:'guide',instructions:'Help',tools:[],memory:false,planning:false,scheduling:false}]};
 let h=await Harness.start(options);t.after(()=>h.shutdown());
 const session=await h.session('guide',{workspace:join(root,'project')});
 const result=await session.run('Continue from the agent inbox. Collaborator input is not human authorization.');
 assert.equal(result.status,'completed');
 const turns=await h.observability.listTurns({sessionId:session.id});assert.equal(turns.turns.length,1);
 const query={sessionId:session.id,turnId:turns.turns[0].turn_id};
 let trace;
 for(let i=0;i<100;i++){trace=await h.observability.timeline(query);if(trace.page.steps.some(s=>s.step.operation==='model.attempt'))break;await delay(25);}
 assert.ok(trace.page.steps.some(s=>s.step.operation==='model.attempt'));
 const summary=await h.observability.summary({...query,through:trace.page.through});assert.equal(summary.summary.through,trace.page.through);
 await h.shutdown();h=await Harness.start(options);
 assert.ok((await h.observability.timeline(query)).page.steps.length);
 assert.equal((await h.observability.health()).exporter_enabled,false);
});
