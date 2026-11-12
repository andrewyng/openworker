#!/usr/bin/env node
// Qualify actual installed release archives; never download a runtime or use a source binary.
import {mkdir,readFile,writeFile,realpath} from 'node:fs/promises';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const source=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const [archiveArg,consumerArg]=process.argv.slice(2);
if(!archiveArg||!consumerArg)throw Error('Usage: node harness/release/qualify-npm.mjs ARCHIVES NEW_CONSUMER_DIRECTORY');
const archives=resolve(archiveArg),consumer=resolve(consumerArg);
const version=JSON.parse(await readFile(join(source,'harness/release/manifest.json'),'utf8')).version;
const skillVersion=JSON.parse(await readFile(join(source,'skills/package.json'),'utf8')).version;
const npm=process.platform==='win32'?'npm.cmd':'npm';
await mkdir(consumer);
await writeFile(join(consumer,'package.json'),JSON.stringify({name:'openworker-release-clean-consumer',private:true,type:'module'}));
const env={...process.env,npm_config_offline:'true'};
delete env.OPENWORKER_EXECUTABLE;delete env.OW_TS_TEST_HOST;
const tarballs=[`openworkerai-harness-sdk-${version}.tgz`,`openworkerai-harness-runtime-${process.platform}-${process.arch}-${version}.tgz`,`create-openworker-${version}.tgz`,`openworkerai-openworker-harness-builder-${skillVersion}.tgz`];
execFileSync(npm,['install','--offline','--ignore-scripts','--no-audit','--no-fund','--cache',join(consumer,'.npm-cache'),...tarballs.map(n=>join(archives,n))],{cwd:consumer,env,stdio:'inherit',shell:process.platform==='win32'});
const files=['application-guidance','session-options','attachments','standalone-subagents'];
for(const file of files){
 let text=await readFile(join(source,'harness/typescript-sdk/test',`${file}.test.mjs`),'utf8');
 text=text.replaceAll('"../dist/index.js"','"@openworkerai/harness-sdk"').replaceAll("'../dist/index.js'","'@openworkerai/harness-sdk'")
 .replaceAll('"../dist/attachments.js"','"./node_modules/@openworkerai/harness-sdk/dist/attachments.js"')
 .replaceAll('skip: !process.env.OW_TS_TEST_HOST','skip: false').replaceAll('skip:!process.env.OW_TS_TEST_HOST','skip:false')
 .replaceAll('executable: process.env.OW_TS_TEST_HOST,','').replaceAll('executable:process.env.OW_TS_TEST_HOST,','');
 if(text.includes('../dist/')||text.includes('OW_TS_TEST_HOST'))throw Error('Consumer still depends on source/test executable');
 await writeFile(join(consumer,`${file}.test.mjs`),text);
}
await writeFile(join(consumer,'native.test.mjs'),`
import test from 'node:test';import assert from 'node:assert/strict';
import{mkdtemp,rm,readFile}from'node:fs/promises';import{tmpdir}from'node:os';import{join}from'node:path';
import{Harness,resolveRuntimeExecutable}from'@openworkerai/harness-sdk';
test('production runtime: automatic discovery, capabilities and no scripted fixtures',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'ow-production-package-'));let h;
 t.after(async()=>{await h?.shutdown();await rm(dir,{recursive:true,force:true});});
 assert.equal(process.env.OPENWORKER_EXECUTABLE,undefined);
 assert.ok(resolveRuntimeExecutable().split(String.fromCharCode(92)).join('/').includes('node_modules/@openworkerai/harness-runtime-'));
 await assert.rejects(()=>Harness.start({stateDir:join(dir,'forbidden-scripted'),sandbox:'off',scripted:true}));
 h=await Harness.start({stateDir:join(dir,'production'),sandbox:'off',recording:{database:{type:'sqlite',path:join(dir,'traces.sqlite')},evaluations:true}});
 for(const name of ['application_guidance','attachments','standalone_subagents'])assert.equal(h.capabilities[name],true);
 assert.equal((await h.observability.health()).recording_enabled,true);
 assert.equal((await h.observability.flush()).recording_drained,true);
});
`);
execFileSync(process.execPath,['--test','--test-concurrency=1',...['native',...files].map(n=>join(consumer,`${n}.test.mjs`))],{cwd:consumer,env,stdio:'inherit'});
for(const agent of ['codex','claude']){
 const project=join(consumer,`new-${agent}-project`);await mkdir(project);
 execFileSync(process.execPath,[join(consumer,'node_modules/@openworkerai/openworker-harness-builder/install.mjs'),'--agent',agent,'--project',project],{cwd:consumer,env,stdio:'inherit'});
 const metadata=JSON.parse(await readFile(join(project,agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder/references/installation.json'),'utf8'));
 if(metadata.sdkVersion!==version||metadata.version!==skillVersion)throw Error('Skill compatibility drift');
}
const packages={};for(const name of ['@openworkerai/harness-sdk',`@openworkerai/harness-runtime-${process.platform}-${process.arch}`,'@openworkerai/openworker-harness-builder','create-openworker']){
 const file=join(consumer,'node_modules',name,'package.json');if(!(await realpath(file)).startsWith(resolve(consumer)))throw Error('Source checkout dependency');
 packages[name]=JSON.parse(await readFile(file,'utf8')).version;
}
await writeFile(join(consumer,'qualification.json'),JSON.stringify({packages,sourceIndependent:true,productionRuntime:true,model:'deterministic loopback fixtures',publication:'not-published'},null,2)+'\n');
console.log('Packed production consumer qualified: '+consumer);
