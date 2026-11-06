#!/usr/bin/env node
// Project-local installation only; never overwrite an existing skill.
import { mkdir, lstat, realpath, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const source = dirname(fileURLToPath(import.meta.url));
const files = ['SKILL.md','references/desktop.md','references/web.md','references/sdk.md','agents/openai.yaml'];
export async function install({agent, project}) {
 if (!['codex','claude'].includes(agent) || !project) throw Error('Use --agent codex|claude --project /path/to/your-app');
 const pkg = JSON.parse(await readFile(join(source,'package.json'),'utf8'));
 // Validate package completeness before creating an installation directory.
 const content = await Promise.all(files.map(name => readFile(join(source,'openworker-apps',name))));
 const root = await realpath(project);
 if (!(await lstat(root)).isDirectory()) throw Error('Project must be an existing directory.');
 let parent = root;
 for (const component of [agent === 'codex' ? '.agents' : '.claude', 'skills']) {
  parent = join(parent, component);
  try { await mkdir(parent); } catch(error) { if(error.code !== 'EEXIST') throw error; }
  const stat = await lstat(parent);
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw Error(`Expected a real directory: ${parent}`);
 }
 const destination = join(parent, 'openworker-apps');
 await mkdir(destination); // Exclusive: an existing install must be reviewed by its owner.
 for (const [index,name] of files.entries()) {
  await mkdir(dirname(join(destination,name)), {recursive:true});
  await writeFile(join(destination,name), content[index], {flag:'wx'});
 }
 await writeFile(join(destination,'references/installation.json'), JSON.stringify({
  package:pkg.name, version:pkg.version, sdkPackage:'@openworkerai/sdk', sdkVersion:pkg.version,
 },null,2)+'\n');
 return destination;
}
if (process.argv[1] && await realpath(process.argv[1]).catch(() => resolve(process.argv[1])) === fileURLToPath(import.meta.url)) {
 const args=process.argv.slice(2);
 if(args.length===1 && args[0]==='--help') console.log('build-with-sdk-skills --agent codex|claude --project /path/to/your-app');
 else {
  try {
   if(args.length!==4 || args[0]!=='--agent' || args[2]!=='--project') throw Error('Use --agent codex|claude --project /path/to/your-app');
   console.log(`Installed: ${await install({agent:args[1],project:args[3]})}\nOpen a new coding-agent session in that project to use openworker-apps.`);
  } catch(error) { console.error(error.code==='EEXIST' ? 'An installation already exists. Review or back it up before replacing it.' : error.message);process.exitCode=1; }
 }
}
