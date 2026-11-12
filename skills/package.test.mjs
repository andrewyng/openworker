import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, mkdir, readdir, rm, writeFile, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { prepare } from './prepare.mjs';

test('packed installer works outside the checkout without SDK or native dependencies', {timeout: 60000}, async () => {
  const root = await mkdtemp(join(tmpdir(), 'ow-packed-skills-'));
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  try {
    const archives = await prepare(join(root, 'candidate'));
    const [archive] = await readdir(archives);
    const consumer = join(root, 'consumer');
    await mkdir(consumer);
    await writeFile(join(consumer, 'package.json'), JSON.stringify({name:'skill-consumer',private:true}));
    execFileSync(npm, ['install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
      '--cache', join(root, 'cache'), join(archives, archive)],
      {cwd:consumer,stdio:'pipe',shell:process.platform==='win32'});
    const installed = join(consumer, 'node_modules/@openworkerai/openworker-harness-builder');
    const pkg = JSON.parse(await readFile(join(installed, 'package.json'), 'utf8'));
    assert.equal(pkg.private, undefined);
    assert.equal(pkg.scripts, undefined);
    assert.equal(pkg.dependencies, undefined);
    assert.equal(pkg.optionalDependencies, undefined);
    assert.deepEqual(await readdir(join(consumer, 'node_modules/@openworkerai')), ['openworker-harness-builder']);
    // Exercise npm's symlink on Unix. On Windows use the installed entry point
    // directly so this test does not require cmd.exe quoting for project paths.
    const bin = join(consumer, 'node_modules/.bin/openworker-harness-builder' + (process.platform==='win32'?'.cmd':''));
    const run = (args) => process.platform === 'win32'
      ? execFileSync(process.execPath,[join(installed,'install.mjs'),...args],{cwd:consumer,encoding:'utf8'})
      : execFileSync(bin,args,{cwd:consumer,encoding:'utf8'});
    assert.match(run(['--help']), /--agent codex\|claude/);
    assert.match(run(['--help']), /--global/);
    // Mock only the child's OS home resolver; never write to the developer's
    // actual home or override HOME/USERPROFILE while exercising the real CLI.
    const userHome=join(root,'personal home');await mkdir(userHome);
    const preload=join(root,'home-fixture.mjs');
    await writeFile(preload,`import os from 'node:os';import{syncBuiltinESMExports}from'node:module';os.homedir=()=>${JSON.stringify(userHome)};syncBuiltinESMExports();`);
    const runGlobal=(args)=>execFileSync(process.execPath,['--import',pathToFileURL(preload).href,join(installed,'install.mjs'),...args],{cwd:consumer,encoding:'utf8',stdio:'pipe'});
    for (const agent of ['codex','claude']) {
      const project = join(root, `new ${agent} app`);
      await mkdir(project);
      assert.match(run(['--agent',agent,'--project',project]), /Installed:/);
      const dest = join(project,agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder');
      const metadata = JSON.parse(await readFile(join(dest,'references/installation.json'),'utf8'));
      assert.equal(metadata.sdkVersion,pkg.openworkerSdkVersion);
      assert.equal(metadata.sdkPackage,'@openworkerai/harness-sdk');
      assert.equal(metadata.version,pkg.version);
      assert.match(runGlobal(['--agent',agent,'--global']),/Installed:/);
      const globalDest=join(userHome,agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder');
      assert.deepEqual(JSON.parse(await readFile(join(globalDest,'references/installation.json'),'utf8')),metadata);
      for(const file of ['SKILL.md','references/railway.md','assets/railway/Dockerfile','assets/railway/.dockerignore'])
        assert.equal(await readFile(join(globalDest,file),'utf8'),await readFile(join(dest,file),'utf8'));
      await assert.rejects(readFile(join(consumer,agent==='codex'?'.agents':'.claude','skills/openworker-harness-builder/SKILL.md')),{code:'ENOENT'});
      await writeFile(join(globalDest,'SKILL.md'),'personal customization');
      assert.throws(()=>runGlobal(['--global','--agent',agent]));
      assert.equal(await readFile(join(globalDest,'SKILL.md'),'utf8'),'personal customization');
      for (const folder of ['', 'references', 'agents']) {
        for (const file of await readdir(join(dest,folder))) {
          if (!/\.(md|json|yaml)$/.test(file)) continue;
          const text = await readFile(join(dest,folder,file),'utf8');
          assert.ok(!text.includes(await realpath(root)), 'installation must not contain temporary machine paths');
          for (const [,link] of text.matchAll(/\]\(([^)]+)\)/g)) {
            if (/^[a-z]+:|^#/.test(link)) continue;
            await readFile(join(dest,folder,link.split('#')[0]));
          }
        }
      }
      await writeFile(join(dest,'SKILL.md'),'developer customization');
      assert.throws(() => run(['--agent',agent,'--project',project]));
      assert.equal(await readFile(join(dest,'SKILL.md'),'utf8'),'developer customization');
    }
  } finally { await rm(root, {recursive:true,force:true}); }
});
