#!/usr/bin/env node
// Install the prepared tarballs outside the checkout, compile and run the guide.
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const version=JSON.parse(await readFile(join(root,'release/manifest.json'),'utf8')).version;
const archives=resolve(process.argv[2]);const dir=await mkdtemp(join(tmpdir(),'ow-packed-consumer-'));
const npm=process.platform==='win32'?'npm.cmd':'npm';
try {
 await writeFile(join(dir,'package.json'),JSON.stringify({name:'packed-sdk-consumer',private:true,type:'module'}));
 execFileSync(npm,['install','--cache',join(dir,'cache'),'--offline','--ignore-scripts','--no-audit','--no-fund',join(archives,`openworkerai-harness-sdk-${version}.tgz`),join(archives,`openworkerai-harness-runtime-${process.platform}-${process.arch}-${version}.tgz`),join(archives,`create-openworker-${version}.tgz`)],{cwd:dir,stdio:'inherit',shell:process.platform==='win32'});
 execFileSync(process.execPath,[join(root,'release/check-examples.mjs'),dir],{stdio:'inherit'});
 execFileSync(process.execPath,[join(root,'typescript-sdk/node_modules/typescript/bin/tsc'),join(dir,'desktop.ts'),'--module','nodenext','--target','es2022','--strict','--skipLibCheck','--typeRoots',join(root,'typescript-sdk/node_modules/@types')],{cwd:dir,stdio:'inherit'});
 execFileSync(process.execPath,[join(dir,'node_modules/create-openworker/create.mjs'),join(dir,'new-app')],{cwd:dir,stdio:'inherit'});
 assert.equal(JSON.parse(await readFile(join(dir,'new-app/openworker.json'),'utf8')).sdkRoot,null);
 // The async fixture must keep its own event loop running while the app executes.
 execFileSync(process.execPath,[join(root,'release/run-examples.mjs'),dir,'-',process.execPath],{cwd:dir,stdio:'inherit'});
 await writeFile(join(dir,'observability.mjs'), `
 import assert from 'node:assert/strict';
 import {Harness,langfuseTelemetry} from '@openworkerai/harness-sdk';
 import {evidenceJsonl} from '@openworkerai/harness-sdk/evaluation-export';
 import {join} from 'node:path';
 const h=await Harness.start({stateDir:join(process.cwd(),'observation-state'),sandbox:'off',recording:{database:{type:'sqlite',path:join(process.cwd(),'observation.sqlite')},evaluations:true}});
 try {assert.equal((await h.observability.health()).recording_enabled,true);assert.equal((await h.observability.flush()).recording_drained,true);assert.equal(langfuseTelemetry('http://127.0.0.1:1','AUTH').acknowledgement,'langfuse_v4');assert(evidenceJsonl({selection:{sessionId:'s',turnId:'t'},watermark:0,timeline:{},summary:{}}).includes('selected_pages_only'));}
 finally {await h.shutdown();}
 `);
 execFileSync(process.execPath,[join(dir,'observability.mjs')],{cwd:dir,stdio:'inherit'});
 console.log('Packed npm client, native runtime, starter and exact TypeScript guide verified');
} finally {await rm(dir,{recursive:true,force:true});}
