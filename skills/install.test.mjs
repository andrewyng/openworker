import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm,mkdir,symlink,readdir,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {install,parseInstallArgs} from './install.mjs';
for (const agent of ['codex','claude']) test(`install complete ${agent} skill; preserve existing content`, async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow skill '));
 try {
  const dest=await install({agent,project:root});
  assert.equal(dest,join(await realpath(root),agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder'));
  const skill=await readFile(join(dest,'SKILL.md'),'utf8');
  assert.match(skill,/^name: openworker-harness-builder$/m);
  for(const [,path] of skill.matchAll(/\]\((references\/[^)]+)\)/g)) assert.ok((await readFile(join(dest,path),'utf8')).length>0);
  assert.ok((await readFile(join(dest,'agents/openai.yaml'),'utf8')).includes('$openworker-harness-builder'));
  const metadata=JSON.parse(await readFile(join(dest,'references/installation.json'),'utf8'));
  const pkg=JSON.parse(await readFile(new URL('./package.json',import.meta.url),'utf8'));
  assert.equal(metadata.sdkPackage,'@openworkerai/harness-sdk');
  assert.equal(metadata.sdkVersion,pkg.openworkerSdkVersion);
  assert.equal(metadata.package,'@openworkerai/openworker-harness-builder');
  assert.equal(metadata.checkout,undefined);
  await writeFile(join(dest,'SKILL.md'),'local customization');
  await assert.rejects(install({agent,project:root}),{code:'EEXIST'});
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'local customization');
 } finally {await rm(root,{recursive:true,force:true});}
});
for (const agent of ['codex','claude']) test(`install ${agent} user-wide independently of project installs`,async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-global-skill-'));
 try {
  const userHome=join(root,'personal home'),project=join(root,'project');
  await mkdir(userHome);await mkdir(project);
  const local=await install({agent,project});
  const destination=await install({agent,global:true},{userHome});
  assert.equal(destination,join(await realpath(userHome),agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder'));
  for(const name of ['SKILL.md','references/railway.md','assets/railway/Dockerfile','assets/railway/.dockerignore'])
   assert.equal(await readFile(join(destination,name),'utf8'),await readFile(join(local,name),'utf8'));
  await writeFile(join(destination,'SKILL.md'),'personal customization');
  await assert.rejects(install({agent,global:true},{userHome}),{code:'EEXIST'});
  assert.equal(await readFile(join(destination,'SKILL.md'),'utf8'),'personal customization');
  assert.notEqual(await readFile(join(local,'SKILL.md'),'utf8'),'personal customization');
 } finally {await rm(root,{recursive:true,force:true});}
});
test('require exactly one scope and reject unknown CLI arguments',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-skill-invalid-scope-'));
 try {
  assert.deepEqual({...parseInstallArgs(['--global','--agent','claude'])},{global:true,agent:'claude'});
  assert.deepEqual({...parseInstallArgs(['--project',root,'--agent','codex'])},{project:root,agent:'codex'});
  for(const args of [[],['--agent','claude'],['--global'],['--agent','claude','--global','--project',root],['--agent','claude','--global','extra'],['--agent','claude','--globall'],['--agent','claude','--project','']])
   assert.throws(()=>parseInstallArgs(args));
  await assert.rejects(install({agent:'claude',project:root,global:true},{userHome:root}),/exactly one/);
  await assert.rejects(install({agent:'claude',global:'yes'},{userHome:root}),/exactly one/);
  assert.deepEqual(await readdir(root),[]);
 } finally {await rm(root,{recursive:true,force:true});}
});
for(const folder of ['.agents','.claude'])test(`user-wide install refuses a linked ${folder} directory`,async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-global-linked-')),outside=await mkdtemp(join(tmpdir(),'ow-global-outside-'));
 try {
  await symlink(outside,join(root,folder),'junction');
  await assert.rejects(install({agent:folder==='.agents'?'codex':'claude',global:true},{userHome:root}),/real directory/);
  assert.deepEqual(await readdir(outside),[]);
 } finally {await rm(root,{recursive:true,force:true});await rm(outside,{recursive:true,force:true});}
});
test('reject invalid agent and linked destination directories',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-skill-scope-'));const outside=await mkdtemp(join(tmpdir(),'ow-skill-other-'));
 try {
  await assert.rejects(install({agent:'other',project:root}));
  assert.deepEqual(await readdir(root),[]);
  await symlink(outside,join(root,'.agents'),'junction');
  await assert.rejects(install({agent:'codex',project:root}),/real directory/);
  assert.deepEqual(await readdir(outside),[]);
 } finally {await rm(root,{recursive:true,force:true});await rm(outside,{recursive:true,force:true});}
});

for(const agent of ['codex','claude'])test(`renamed ${agent} skill preserves the legacy install`,async()=>{
 const project=await mkdtemp(join(tmpdir(),'ow-skill-rename-'));
 try {
  const legacy=join(project,agent==='codex'?'.agents':'.claude','skills/openworker-apps');
  await mkdir(legacy,{recursive:true});
  await writeFile(join(legacy,'SKILL.md'),'legacy customization');
  const current=await install({agent,project});
  assert.notEqual(current,legacy);
  assert.equal(await readFile(join(legacy,'SKILL.md'),'utf8'),'legacy customization');
  assert.match(await readFile(join(current,'SKILL.md'),'utf8'),/^name: openworker-harness-builder$/m);
 }finally{await rm(project,{recursive:true,force:true});}
});
