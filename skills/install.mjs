#!/usr/bin/env node
// Project or user-wide installation; never overwrite an existing skill.
import { mkdir, lstat, realpath, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { parseArgs } from 'node:util';
const source = dirname(fileURLToPath(import.meta.url));
const usage = 'openworker-harness-builder --agent codex|claude (--project /path/to/your-app | --global)';
const files = ['SKILL.md','references/desktop.md','references/web.md','references/sdk.md','references/runtime-options.md','references/subagents.md','references/observability.md','references/railway.md','assets/railway/Dockerfile','assets/railway/.dockerignore','agents/openai.yaml'];
function validate({agent, project, global: userWide = false}) {
 if (!['codex','claude'].includes(agent) || typeof userWide !== 'boolean' ||
     (project !== undefined && (typeof project !== 'string' || !project.trim())) ||
     userWide === (project !== undefined)) throw Error(`Use ${usage}; choose exactly one installation scope.`);
}
export function parseInstallArgs(args) {
 const {values} = parseArgs({args, options:{agent:{type:'string'}, project:{type:'string'}, global:{type:'boolean'}}});
 validate(values);
 return values;
}
export async function install(options, {userHome = homedir()} = {}) {
 validate(options);
 const {agent, project, global: userWide = false} = options;
 const pkg = JSON.parse(await readFile(join(source,'package.json'),'utf8'));
 // Validate package completeness before creating an installation directory.
 const content = await Promise.all(files.map(name => readFile(join(source,'openworker-harness-builder',name))));
 const root = await realpath(userWide ? userHome : project);
 if (!(await lstat(root)).isDirectory()) throw Error('Installation root must be an existing directory.');
 let parent = root;
 for (const component of [agent === 'codex' ? '.agents' : '.claude', 'skills']) {
  parent = join(parent, component);
  try { await mkdir(parent); } catch(error) { if(error.code !== 'EEXIST') throw error; }
  const stat = await lstat(parent);
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw Error(`Expected a real directory: ${parent}`);
 }
 const destination = join(parent, 'openworker-harness-builder');
 await mkdir(destination); // Exclusive: an existing install must be reviewed by its owner.
 for (const [index,name] of files.entries()) {
  await mkdir(dirname(join(destination,name)), {recursive:true});
  await writeFile(join(destination,name), content[index], {flag:'wx'});
 }
 await writeFile(join(destination,'references/installation.json'), JSON.stringify({
  package:pkg.name, version:pkg.version, sdkPackage:'@openworkerai/harness-sdk', sdkVersion:pkg.openworkerSdkVersion,
 },null,2)+'\n');
 return destination;
}
if (process.argv[1] && await realpath(process.argv[1]).catch(() => resolve(process.argv[1])) === fileURLToPath(import.meta.url)) {
 const args=process.argv.slice(2);
 if(args.length===1 && args[0]==='--help') console.log(`${usage}\n--global installs for your user across projects on this machine (Codex: ~/.agents/skills; Claude Code: ~/.claude/skills).`);
 else {
  try {
   const options = parseInstallArgs(args);
   console.log(`Installed: ${await install(options)}\nOpen a new coding-agent session${options.global ? '' : ' in that project'} to use openworker-harness-builder.`);
  } catch(error) { console.error(error.code==='EEXIST' ? 'An installation already exists. Review or back it up before replacing it.' : error.message);process.exitCode=1; }
 }
}
