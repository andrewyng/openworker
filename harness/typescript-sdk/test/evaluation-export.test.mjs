import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {langfuseScore,exportLangfuseScore,evidenceJsonl} from '../dist/evaluation-export.js';
const evaluation={version:1,deployment:'fixture',target:{session_hash:'1'.repeat(64),turn_hash:'2'.repeat(64),trace_id:'3'.repeat(32),span_id:'4'.repeat(16)},result:{id:'quality-1',name:'correctness',evaluator:{name:'code',version:'1',source:'code'},status:'completed',score:{type:'boolean',value:true},created_ms:1000,explanation:'private explanation'}};
test('score identity and timestamp are stable; explanation is opt-in',()=>{
 const a=langfuseScore(evaluation);assert.deepEqual(a,langfuseScore(evaluation));assert.equal(a.timestamp,'1970-01-01T00:00:01.000Z');assert.equal(a.comment,undefined);assert.equal(a.value,1);assert.equal(langfuseScore(evaluation,true).comment,'private explanation');assert.throws(()=>langfuseScore({...evaluation,target:{...evaluation.target,trace_id:null}}));
});
test('export sends only score endpoint and never follows redirects or retries',async t=>{
 let calls=0;let mode='ok';const server=createServer(async(req,res)=>{calls++;let text='';for await(const c of req)text+=c;assert.equal(req.url,'/api/public/scores');const body=JSON.parse(text);if(mode==='redirect'){res.writeHead(302,{location:'/unwanted'});res.end();}else {res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({id:body.id}));}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>{server.closeAllConnections();server.close();delete process.env.OW_TEST_EVAL_AUTH;});process.env.OW_TEST_EVAL_AUTH='synthetic-fixture-only';
 const options={url:`http://127.0.0.1:${server.address().port}`,authorizationEnv:'OW_TEST_EVAL_AUTH'};
 assert.equal((await exportLangfuseScore(evaluation,options)).id,langfuseScore(evaluation).id);mode='redirect';await assert.rejects(exportLangfuseScore(evaluation,options),/unknown/);assert.equal(calls,2);
});
test('JSONL manifest names coverage and content explicitly',()=>{
 const bundle=evidenceJsonl({selection:{sessionId:'s',turnId:'t'},watermark:4,timeline:{},summary:{},evaluations:[evaluation]});
 const [m]=bundle.trim().split('\n').map(JSON.parse);assert.equal(m.contentIncluded,false);assert.equal(m.coverage,'selected_pages_only');assert.equal(m.sha256.length,64);
});
