// Exercise the exact installed builder archive without changing the user's skills.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync,spawnSync} from 'node:child_process';

export async function checkBuilder(consumer,expected) {
 const installed=join(consumer,'node_modules/@openworkerai/openworker-harness-builder');
 const pkg=JSON.parse(await readFile(join(installed,'package.json'),'utf8'));
 assert.equal(pkg.name,expected.name);assert.equal(pkg.version,expected.version);
 assert.equal(pkg.openworkerSdkVersion,expected.openworkerSdkVersion);
 assert.equal(pkg.private,undefined);assert.equal(pkg.dependencies,undefined);
 assert.equal(pkg.optionalDependencies,undefined);
 const userHome=join(consumer,'isolated user home');await mkdir(userHome);
 const preload=join(consumer,'builder-home.mjs');
 await writeFile(preload,`import os from 'node:os';import {syncBuiltinESMExports} from 'node:module';os.homedir=()=>${JSON.stringify(userHome)};syncBuiltinESMExports();`);
 const env={...process.env};delete env.OPENWORKER_EXECUTABLE;
 const cli=join(installed,'install.mjs');
 for(const agent of ['codex','claude'])for(const global of [false,true]) {
  const project=join(consumer,`fresh ${agent} project`);if(!global)await mkdir(project);
  const args=[...(global?['--import',pathToFileURL(preload).href]:[]),cli,'--agent',agent,...(global?['--global']:['--project',project])];
  const options={cwd:consumer,env,encoding:'utf8',stdio:['ignore','pipe','pipe']};
  assert.match(execFileSync(process.execPath,args,options),/Installed:/);
  const dest=join(global?userHome:project,agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder');
  const metadata=JSON.parse(await readFile(join(dest,'references/installation.json'),'utf8'));
  assert.equal(metadata.sdkVersion,expected.openworkerSdkVersion);
  assert.equal(metadata.version,expected.version);
  assert.match(await readFile(join(dest,'SKILL.md'),'utf8'),/^name: openworker-harness-builder$/m);
  await writeFile(join(dest,'SKILL.md'),'preserve this customization');
  assert.equal(spawnSync(process.execPath,args,options).status,1);
  assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'preserve this customization');
  const replaced=execFileSync(process.execPath,[...args,'--replace-existing'],options);
  const backup=replaced.match(/^Backup saved: (.+)$/m)?.[1].trim();assert(backup);
  assert.equal(await readFile(join(backup,'SKILL.md'),'utf8'),'preserve this customization');
  assert.match(await readFile(join(dest,'SKILL.md'),'utf8'),/^name: openworker-harness-builder$/m);
 }
 console.log('Exact builder archive: Codex/Claude project/global installs and protected replacement backups passed');
}
