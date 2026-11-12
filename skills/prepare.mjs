#!/usr/bin/env node
// Prepare an independently installable skill package. Never publish or install it.
import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const source = dirname(fileURLToPath(import.meta.url));
export async function prepare(directory) {
  const out = resolve(directory);
  const pkg = JSON.parse(await readFile(join(source, 'package.json'), 'utf8'));
  // The independently published Skill pins a tested SDK, which need not be
  // this checkout's native-release candidate. Do not rewrite that consumer pin.
  if (typeof pkg.openworkerSdkVersion !== 'string' ||
      !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$/.test(pkg.openworkerSdkVersion))
    throw Error('Skill SDK compatibility must pin an exact release version');
  await mkdir(out); // An existing candidate must never be overwritten.
  const staging = join(out, 'package');
  await mkdir(staging);
  for (const file of pkg.files) await cp(join(source, file), join(staging, file), { recursive: true });
  delete pkg.private;
  delete pkg.scripts;
  await writeFile(join(staging, 'package.json'), JSON.stringify(pkg, null, 2) + '\n');
  const archives = join(out, 'archives');
  await mkdir(archives);
  execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['pack', '--offline', '--ignore-scripts', '--json', '--cache', join(out, '.npm-cache'), '--pack-destination', archives],
    { cwd: staging, stdio: 'inherit', shell: process.platform === 'win32' });
  return archives;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3) throw Error('Usage: node skills/prepare.mjs NEW_OUTPUT_DIRECTORY');
  console.log(`Prepared ${await prepare(process.argv[2])}. Nothing published.`);
}
