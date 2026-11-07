#!/usr/bin/env node
// Run the guide programs against a loopback Responses fixture, never a real key.
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';
const [consumer,rustExecutable,helper]=process.argv.slice(2);
if(!consumer||!rustExecutable||!helper)throw Error('Usage: run-examples.mjs NPM_CONSUMER RUST_EXAMPLE_EXE HARNESS_EXE');
let requests=0;let fixtureError;
const server=createServer(async(req,res)=>{
 try {
  const chunks=[];for await(const part of req)chunks.push(part);const body=JSON.parse(Buffer.concat(chunks));
  assert.equal(body.model,'fixture');assert.equal(req.url,'/v1/responses');requests++;
  res.writeHead(200,{'content-type':'text/event-stream'});
  res.write('data: '+JSON.stringify({type:'response.output_text.delta',delta:'A tiny memory game.'})+'\n\n');
  res.end('data: '+JSON.stringify({type:'response.completed',response:{status:'completed',output:[{type:'message',role:'assistant',content:[{type:'output_text',text:'A tiny memory game.'}]}],usage:{input_tokens:20,output_tokens:8}}})+'\n\n');
 }catch(e){fixtureError=e;res.destroy();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const home=await mkdtemp(join(tmpdir(),'ow-docs-home-'));
try {
 const programs=[['typescript',process.execPath,[join(resolve(consumer),'desktop.js')]]];
 if(rustExecutable!=='-')programs.push(['rust',resolve(rustExecutable),[]]);
 for(const [name,exe,args]of programs) {
  const dir=join(home,name);await mkdir(dir);
  const env={...process.env,HOME:dir,USERPROFILE:dir,OPENAI_API_KEY:'fixture-key-not-real',OPENWORKER_MODEL:'fixture',OPENWORKER_MODEL_URL:`http://127.0.0.1:${server.address().port}/v1`};
  // TS must discover the installed npm runtime; Rust uses the documented explicit helper.
  delete env.OPENWORKER_EXECUTABLE;if(name==='rust')env.OPENWORKER_EXECUTABLE=resolve(helper);
  const output=await new Promise((accept,reject)=>{
   const child=spawn(exe,args,{cwd:dir,env,stdio:['ignore','pipe','pipe']});let output='';const timer=setTimeout(()=>{child.kill();reject(Error(`${name} timeout`));},60000);
   child.stdout.on('data',d=>output+=d);child.stderr.on('data',d=>output+=d);child.on('error',reject);child.on('close',code=>{clearTimeout(timer);code===0?accept(output):reject(Error(`${name} failed: ${output}`));});
  });
  assert.match(output,/A tiny memory game/);assert.match(output,/completed/i);console.log(`${name}: exact Markdown program completed with streaming against local fixture`);
 }
 assert.equal(requests,rustExecutable==='-'?1:2);assert.equal(fixtureError,undefined);
} finally {server.closeAllConnections();await new Promise(r=>server.close(r));await rm(home,{recursive:true,force:true});}
