import test from 'node:test';
import { mkdtemp, readFile, mkdir, symlink, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

test('Skill options and README approval loop compile against the SDK declarations', async () => {
  const sdk = resolve('harness/typescript-sdk');
  const root = await mkdtemp(join(tmpdir(), 'ow-skill-snippets-'));
  try {
    await mkdir(join(root, 'node_modules/@openworkerai'), { recursive: true });
    await symlink(sdk, join(root, 'node_modules/@openworkerai/harness-sdk'), 'junction');
    await writeFile(join(root, 'package.json'), '{"type":"module"}');
    const readme = await readFile(join(sdk,'README.md'),'utf8');
    const approvalSection = readme.split('### Stream, approve and continue the same turn')[1]?.split('### Auto-approver decisions in the UI')[0];
    if (!approvalSection) throw Error('Missing README approval loop');
    const text = (await Promise.all(['runtime-options.md','subagents.md'].map(name=>readFile('skills/openworker-harness-builder/references/'+name,'utf8')))).join('\n')+'\n'+approvalSection;
    const files = [];
    for (const [index, match] of [...text.matchAll(/```typescript\r?\n([\s\S]*?)```/g)].entries()) {
      const file = join(root, `example-${index}.ts`);
      await writeFile(file, match[1]);
      files.push(file);
    }
    if (!files.length) throw Error('No examples found');
    execFileSync(process.execPath, [join(sdk, 'node_modules/typescript/bin/tsc'), ...files,
      '--noEmit', '--strict', '--skipLibCheck', '--target', 'es2022', '--module', 'nodenext',
      '--typeRoots', join(sdk, 'node_modules/@types')], { stdio: 'inherit' });
  } finally { await rm(root, { recursive: true, force: true }); }
});
