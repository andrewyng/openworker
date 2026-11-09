import test from 'node:test';
import { mkdtemp, readFile, mkdir, symlink, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

test('runtime-options examples compile against the matching SDK declarations', async () => {
  const sdk = resolve('harness/typescript-sdk');
  const root = await mkdtemp(join(tmpdir(), 'ow-skill-snippets-'));
  try {
    await mkdir(join(root, 'node_modules/@openworkerai'), { recursive: true });
    await symlink(sdk, join(root, 'node_modules/@openworkerai/harness-sdk'), 'junction');
    await writeFile(join(root, 'package.json'), '{"type":"module"}');
    const text = await readFile('skills/openworker-apps/references/runtime-options.md', 'utf8');
    const files = [];
    for (const [index, match] of [...text.matchAll(/```typescript\r?\n([\s\S]*?)```/g)].entries()) {
      const file = join(root, `example-${index}.ts`);
      await writeFile(file, match[1]);
      files.push(file);
    }
    if (!files.length) throw Error('No examples found');
    execFileSync(process.execPath, [join(sdk, 'node_modules/typescript/bin/tsc'), ...files,
      '--noEmit', '--strict', '--skipLibCheck', '--target', 'es2022', '--module', 'nodenext',
      '--typeRoots', join(sdk, 'node_modules/@types')], { stdio: 'pipe' });
  } finally { await rm(root, { recursive: true, force: true }); }
});
