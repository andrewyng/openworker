import test from 'node:test';
import { mkdtemp, readFile, mkdir, symlink, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

test('Skill, SDK READMEs and shared guide examples compile against the SDK declarations', async () => {
  const sdk = resolve('harness/typescript-sdk');
  const root = await mkdtemp(join(tmpdir(), 'ow-skill-snippets-'));
  try {
    await mkdir(join(root, 'node_modules/@openworkerai'), { recursive: true });
    await symlink(sdk, join(root, 'node_modules/@openworkerai/harness-sdk'), 'junction');
    await writeFile(join(root, 'package.json'), '{"type":"module"}');
    const readme = await readFile(join(sdk,'README.md'),'utf8');
    const landing = await readFile('harness/README.md','utf8');
    const landingParts = [...landing.matchAll(/```typescript\r?\n([\s\S]*?)```/g)].map(match => match[1]);
    if (landingParts.length !== 4) throw Error('Expected three SDK steps and one complete example');
    // Compile the reference steps together, supplying only their shared imports/model.
    const reference = "import { resolve } from 'node:path';\nimport { Harness } from '@openworkerai/harness-sdk';\nconst model = 'fixture';\n" + landingParts.slice(0, 3).join('\n');
    await writeFile(join(root, 'landing-reference.ts'), reference);
    const streaming = await readFile('docs/harness/guides/streaming.md','utf8');
    const approvalSection = streaming.split('### Stream, approve and continue the same turn')[1];
    if (!approvalSection) throw Error('Missing shared streaming approval loop');
    const tools = await readFile('docs/harness/guides/tools.md','utf8');
    const callbackSection = tools.split('## Register an application callback')[1]?.split('## Tool names and directory scope')[0];
    if (!callbackSection) throw Error('Missing app callback example');
    const text = (await Promise.all(['runtime-options.md','subagents.md'].map(name=>readFile('skills/openworker-harness-builder/references/'+name,'utf8')))).join('\n')+'\n'+readme+'\n```typescript\n'+landingParts[3]+'\n```\n'+approvalSection+'\n'+callbackSection;
    const files = [join(root, 'landing-reference.ts')];
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
