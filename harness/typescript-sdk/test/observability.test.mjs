import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Observability,ServiceObservability,langfuseTelemetry} from '../dist/observability.js';
import {hostConfig} from '../dist/config.js';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
test('local query scopes and bounds are forwarded without widening',async()=>{
 const seen=[];const adapter=new Observability(async(m,p)=>{seen.push([m,p]);return {};});
 await adapter.timeline({sessionId:'s-1',turnId:'t-1',through:42,limit:12});
 assert.deepEqual(seen[0],['observability/timeline',{session_id:'s-1',turn_id:'t-1',through:42,after:0,limit:12}]);
 await assert.rejects(adapter.timeline({sessionId:'../other',turnId:'t'}));
 await assert.rejects(adapter.timeline({sessionId:'s',turnId:'t',limit:10000}));
 assert.equal(seen.length,1);
});
test('service adapter uses existing authorized routes and watermark',async()=>{
 const seen=[];const a=new ServiceObservability(async p=>{seen.push(p);return {};});
 await a.summary({sessionId:'s',turnId:'t',through:9});
 await a.search({q:'a/b ?'});
 assert.equal(seen[0],'/v1/sessions/s/observability/t/summary?through=9');
 assert.equal(seen[1],'/v1/observability/runs?q=a%2Fb+%3F');
});
test('recording is off by default; direct Langfuse uses typed receipts and secret reference',()=>{
 const base={executable:join(tmpdir(),'harness'),stateDir:join(tmpdir(),'state')};
 assert.equal(hostConfig(base).recording,undefined);
 const telemetry=langfuseTelemetry('https://cloud.langfuse.com/api/public/otel','MY_EXPORT_AUTH');
 const wire=hostConfig({...base,telemetry,recording:{database:{type:'sqlite',path:join(tmpdir(),'traces.sqlite')},retentionDays:7}});
 assert.equal(wire.telemetry.acknowledgement,'langfuse_v4');
 assert.equal(wire.telemetry.content.capture,'metadata');
 assert.deepEqual(wire.telemetry.signals,['traces']);
 assert.equal(wire.recording.retention_days,7);
 assert.equal(wire.telemetry.headers[0].value,undefined);
});
test('PostgreSQL recording uses a distinct explicit credential reference',()=>{
 const c=hostConfig({executable:join(tmpdir(),'harness'),stateDir:join(tmpdir(),'state'),recording:{database:{type:'postgres',connectionEnv:'OBS_DB',schema:'ow_obs',loopbackPlaintext:true}}});
 assert.deepEqual(c.recording.database,{kind:'postgres',connection_env:'OBS_DB',schema:'ow_obs',ca_file:null,loopback_plaintext:true});
 assert.equal(c.storage.kind,'sqlite');
});
