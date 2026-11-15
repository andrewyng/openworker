import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm,mkdir,symlink,readdir,realpath,rename,lstat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,relative,sep} from 'node:path';
import {PassThrough} from 'node:stream';
import {install,parseInstallArgs,runCli} from './install.mjs';
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
  assert.deepEqual({...parseInstallArgs(['--agent','claude','--global','--replace-existing'])},{agent:'claude',global:true,replaceExisting:true});
  for(const args of [[],['--agent','claude'],['--global'],['--agent','claude','--global','--project',root],['--agent','claude','--global','extra'],['--agent','claude','--globall'],['--agent','claude','--project','']])
   assert.throws(()=>parseInstallArgs(args));
  await assert.rejects(install({agent:'claude',project:root,global:true},{userHome:root}),/exactly one/);
  await assert.rejects(install({agent:'claude',global:'yes'},{userHome:root}),/exactly one/);
  await assert.rejects(install({agent:'claude',global:true,replaceExisting:'yes'},{userHome:root}),/exactly one/);
  assert.deepEqual(await readdir(root),[]);
 } finally {await rm(root,{recursive:true,force:true});}
});

for(const agent of ['codex','claude'])for(const global of [false,true]) {
 const scope = global ? 'global' : 'project';
 test(`${agent} ${scope} replacement backs up custom files and removes obsolete active files`,async()=>{
  const root=await mkdtemp(join(tmpdir(),'ow-replace-'));
  try {
   const options={agent,...(global?{global:true}:{project:root})};
   const dest=await install(options,{userHome:root});
   await writeFile(join(dest,'SKILL.md'),'my customization');
   await writeFile(join(dest,'obsolete.md'),'old reference');
   const backups=[];
   assert.equal(await install({...options,replaceExisting:true},{userHome:root,
    confirmReplace:()=>{throw Error('Flag must not prompt');},onBackup:path=>backups.push(path)}),dest);
   assert.equal(await readFile(join(backups[0],'SKILL.md'),'utf8'),'my customization');
   assert.equal(await readFile(join(backups[0],'obsolete.md'),'utf8'),'old reference');
   assert.ok(relative(dirname(dest),backups[0]).startsWith('..'+sep),'Backup is outside active skills');
   assert.match(await readFile(join(dest,'SKILL.md'),'utf8'),/^name: openworker-harness-builder$/m);
   await assert.rejects(lstat(join(dest,'obsolete.md')),{code:'ENOENT'});
   await install({...options,replaceExisting:true},{userHome:root,onBackup:path=>backups.push(path)});
   assert.notEqual(backups[0],backups[1]);
   assert.equal(await readFile(join(backups[0],'SKILL.md'),'utf8'),'my customization');
   assert.deepEqual(await readdir(dirname(dest)),['openworker-harness-builder']);
  }finally{await rm(root,{recursive:true,force:true});}
 });
 test(`${agent} ${scope} CLI prompt accepts yes and defaults to no`,async()=>{
  const root=await mkdtemp(join(tmpdir(),'ow-prompt-'));
  try {
   const options={agent,...(global?{global:true}:{project:root})};
   const args=['--agent',agent,...(global?['--global']:['--project',root])];
   const dest=await install(options,{userHome:root});
   await writeFile(join(dest,'SKILL.md'),'prompt customization');
   async function invoke(answer,tty=true) {
    const input=new PassThrough(),output=new PassThrough(),errorOutput=new PassThrough();
    input.isTTY=tty;output.isTTY=tty;
    let text='',errors='',answered=false;
    output.on('data',chunk=>{
     text+=chunk.toString();
     if(!answered&&text.includes('[y/N]')){answered=true;queueMicrotask(()=>input.end(answer===null?'':answer+'\n'));}
    });
    errorOutput.on('data',chunk=>{errors+=chunk.toString();});
    const code=await runCli(args,{userHome:root,input,output,errorOutput});
    input.destroy();output.destroy();errorOutput.destroy();
    return {code,text,errors};
   }
   for(const answer of ['n','',null]) {
    const result=await invoke(answer);
    assert.equal(result.code,0);assert.match(result.text,/cancelled/);
    assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'prompt customization');
   }
   const noninteractive=await invoke('y',false);
   assert.equal(noninteractive.code,1);assert.match(noninteractive.errors,/--replace-existing/);
   assert.ok(!noninteractive.text.includes('[y/N]'));
   const result=await invoke('yes');
   assert.equal(result.code,0);assert.match(result.text,/Backup saved:/);
   const backup=result.text.split('Backup saved: ')[1].split('\n')[0];
   assert.equal(await readFile(join(backup,'SKILL.md'),'utf8'),'prompt customization');
   assert.match(await readFile(join(dest,'SKILL.md'),'utf8'),/^name: openworker-harness-builder$/m);
  }finally{await rm(root,{recursive:true,force:true});}
 });
}

test('failed activation restores the complete previous installation',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-rollback-'));
 try {
  const options={agent:'claude',project:root};
  const dest=await install(options);
  await writeFile(join(dest,'SKILL.md'),'original');
  await writeFile(join(dest,'custom.txt'),'keep me');
  await assert.rejects(install({...options,replaceExisting:true},{renamePath:async(from,to)=>{
   if(from.includes('.harness-builder-stage-'))throw Error('Simulated activation failure');
   await rename(from,to);
  }}),/previous installation was restored/);
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'original');
  assert.equal(await readFile(join(dest,'custom.txt'),'utf8'),'keep me');
  const settings=dirname(dirname(dest));
  assert.deepEqual((await readdir(settings)).sort(),['skill-backups','skills']);
  assert.deepEqual(await readdir(join(settings,'skill-backups')),[]);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('replacement rejects linked skill and backup directories without modifying existing files',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-replace-links-'));
 const outside=await mkdtemp(join(tmpdir(),'ow-replace-outside-'));
 try {
  const options={agent:'codex',project:root};
  const dest=await install(options);
  await writeFile(join(dest,'SKILL.md'),'original');
  await symlink(outside,join(root,'.agents/skill-backups'),'junction');
  await assert.rejects(install({...options,replaceExisting:true}),/real directory/);
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'original');
  assert.deepEqual(await readdir(outside),[]);
  await rm(dest,{recursive:true});
  await symlink(outside,dest,'junction');
  await assert.rejects(install({...options,replaceExisting:true}),/real skill directory/);
  assert.deepEqual(await readdir(outside),[]);
 }finally{await rm(root,{recursive:true,force:true});await rm(outside,{recursive:true,force:true});}
});

test('backup failure leaves the active copy unchanged; failed rollback retains the original backup',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-backup-failure-'));
 try {
  const options={agent:'claude',project:root};
  const dest=await install(options);
  await writeFile(join(dest,'SKILL.md'),'original');
  const backupRoot=join(root,'.claude/skill-backups');
  await writeFile(backupRoot,'blocked backup path');
  await assert.rejects(install({...options,replaceExisting:true}),/real directory/);
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'original');
  await rm(backupRoot);
  await assert.rejects(install({...options,replaceExisting:true},{renamePath:async(from,to)=>{
   if(from!==dest)throw Error('Simulated activation and restoration failure');
   await rename(from,to);
  }}),/backup is preserved at/);
  const backups=await readdir(backupRoot);
  assert.equal(backups.length,1);
  assert.equal(await readFile(join(backupRoot,backups[0],'openworker-harness-builder/SKILL.md'),'utf8'),'original');
  await assert.rejects(lstat(join(root,'.claude/.openworker-harness-builder.install-lock')),{code:'ENOENT'});
 }finally{await rm(root,{recursive:true,force:true});}
});

test('concurrent installer cannot replace an installation being reviewed',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-install-lock-'));
 try {
  const options={agent:'claude',project:root};await install(options);
  let reached,finish;
  const reviewing=new Promise(done=>{reached=done;});
  const decision=new Promise(done=>{finish=done;});
  const first=install(options,{confirmReplace:()=>{reached();return decision;}});
  const declined=assert.rejects(first,{code:'INSTALL_CANCELLED'});
  await reviewing;
  await assert.rejects(install({...options,replaceExisting:true}),/Installation lock exists/);
  finish(false);await declined;
 }finally{await rm(root,{recursive:true,force:true});}
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
