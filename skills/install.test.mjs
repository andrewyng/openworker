import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm,mkdir,symlink,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {install} from './install.mjs';
for (const agent of ['codex','claude']) test(`install complete ${agent} skill; preserve existing content`, async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow skill '));
 try {
  const dest=await install({agent,project:root});
  const skill=await readFile(join(dest,'SKILL.md'),'utf8');
  for(const [,path] of skill.matchAll(/\]\((references\/[^)]+)\)/g)) assert.ok((await readFile(join(dest,path),'utf8')).length>0);
  assert.ok((await readFile(join(dest,'agents/openai.yaml'),'utf8')).includes('$openworker-apps'));
  assert.ok(JSON.parse(await readFile(join(dest,'references/checkout.json'),'utf8')).checkout);
  await writeFile(join(dest,'SKILL.md'),'local customization');
  await assert.rejects(install({agent,project:root}),{code:'EEXIST'});
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'local customization');
 } finally {await rm(root,{recursive:true,force:true});}
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
