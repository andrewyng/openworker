import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, mkdir, readdir, rm, writeFile, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
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
    const installed = join(consumer, 'node_modules/@openworkerai/build-with-sdk-skills');
    const pkg = JSON.parse(await readFile(join(installed, 'package.json'), 'utf8'));
    assert.equal(pkg.private, undefined);
    assert.equal(pkg.scripts, undefined);
    assert.equal(pkg.dependencies, undefined);
    assert.equal(pkg.optionalDependencies, undefined);
    assert.deepEqual(await readdir(join(consumer, 'node_modules/@openworkerai')), ['build-with-sdk-skills']);
    // Exercise npm's symlink on Unix. On Windows use the installed entry point
    // directly so this test does not require cmd.exe quoting for project paths.
    const bin = join(consumer, 'node_modules/.bin/build-with-sdk-skills' + (process.platform==='win32'?'.cmd':''));
    const run = (args) => process.platform === 'win32'
      ? execFileSync(process.execPath,[join(installed,'install.mjs'),...args],{cwd:consumer,encoding:'utf8'})
      : execFileSync(bin,args,{cwd:consumer,encoding:'utf8'});
    assert.match(run(['--help']), /--agent codex\|claude/);
    for (const agent of ['codex','claude']) {
      const project = join(root, `new ${agent} app`);
      await mkdir(project);
      assert.match(run(['--agent',agent,'--project',project]), /Installed:/);
      const dest = join(project,agent==='codex'?'.agents':'.claude','skills/openworker-apps');
      const metadata = JSON.parse(await readFile(join(dest,'references/installation.json'),'utf8'));
      assert.equal(metadata.sdkVersion,pkg.version);
      assert.equal(metadata.sdkPackage,'@openworkerai/harness-sdk');
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
