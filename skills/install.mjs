#!/usr/bin/env node
// Project or user-wide installation with explicit replacement and retained backups.
import { mkdir, mkdtemp, lstat, realpath, readFile, writeFile, rename, rm, rmdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { parseArgs } from 'node:util';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout, stderr } from 'node:process';
const source = dirname(fileURLToPath(import.meta.url));
const usage = 'openworker-harness-builder --agent codex|claude (--project /path/to/your-app | --global) [--replace-existing]';
const files = ['SKILL.md','references/desktop.md','references/web.md','references/sdk.md','references/tools.md','references/approvals.md','references/local-script.md','references/runtime-options.md','references/subagents.md','references/observability.md','references/railway.md','assets/railway/Dockerfile','assets/railway/.dockerignore','assets/local-script/file-tools.mjs','assets/local-script/organizer.mjs','agents/openai.yaml'];
function validate({agent, project, global: userWide = false, replaceExisting = false}) {
 if (!['codex','claude'].includes(agent) || typeof userWide !== 'boolean' || typeof replaceExisting !== 'boolean' ||
     (project !== undefined && (typeof project !== 'string' || !project.trim())) ||
     userWide === (project !== undefined)) throw Error(`Use ${usage}; choose exactly one installation scope.`);
}
export function parseInstallArgs(args) {
 const {values} = parseArgs({args, options:{agent:{type:'string'}, project:{type:'string'}, global:{type:'boolean'}, 'replace-existing':{type:'boolean'}}});
 const {'replace-existing': replaceExisting, ...options} = values;
 if (replaceExisting !== undefined) options.replaceExisting = replaceExisting;
 validate(options);
 return options;
}
async function realDirectory(path) {
 try { await mkdir(path); } catch(error) { if(error.code !== 'EEXIST') throw error; }
 const stat = await lstat(path);
 if (stat.isSymbolicLink() || !stat.isDirectory()) throw Error(`Expected a real directory: ${path}`);
}
async function existingDirectory(path) {
 try {
  const stat = await lstat(path);
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw Error(`Expected a real skill directory: ${path}`);
  return stat;
 } catch(error) { if(error.code === 'ENOENT') return null; throw error; }
}
export async function install(options, {
 userHome = homedir(), confirmReplace, onBackup = () => {}, renamePath = rename,
} = {}) {
 validate(options);
 const {agent, project, global: userWide = false, replaceExisting = false} = options;
 const pkg = JSON.parse(await readFile(join(source,'package.json'),'utf8'));
 // Read and validate the complete package before touching an existing install.
 const content = await Promise.all(files.map(name => readFile(join(source,'openworker-harness-builder',name))));
 const root = await realpath(userWide ? userHome : project);
 if (!(await lstat(root)).isDirectory()) throw Error('Installation root must be an existing directory.');
 const settings = join(root, agent === 'codex' ? '.agents' : '.claude');
 await realDirectory(settings);
 const parent = join(settings, 'skills'); await realDirectory(parent);
 const destination = join(parent, 'openworker-harness-builder');
 // Serialize installers; an interrupted process leaves a visible, reviewable lock.
 const lock = join(settings, '.openworker-harness-builder.install-lock');
 try { await mkdir(lock); }
 catch(error) {
  if(error.code !== 'EEXIST') throw error;
  throw Error(`Installation lock exists: ${lock}. If no installer is running, review and remove that lock directory before retrying.`);
 }
 let stage, backup, backupContainer, moved = false;
 try {
  const previous = await existingDirectory(destination);
  if (previous && !replaceExisting) {
   if (!confirmReplace) throw Object.assign(Error('An installation already exists. Run interactively to review replacement, or use --replace-existing to replace it with a backup.'), {code:'EEXIST'});
   if (await confirmReplace(destination) !== true)
    throw Object.assign(Error('Installation cancelled; existing skill unchanged.'), {code:'INSTALL_CANCELLED'});
  }
  // Staging and backups stay outside the watched skills directory, so neither
  // partial installs nor old copies can be discovered as additional skills.
  stage = await mkdtemp(join(settings, '.harness-builder-stage-'));
  for (const [index,name] of files.entries()) {
   await mkdir(dirname(join(stage,name)), {recursive:true});
   await writeFile(join(stage,name), content[index], {flag:'wx'});
  }
  await writeFile(join(stage,'references/installation.json'), JSON.stringify({
   package:pkg.name, version:pkg.version, sdkPackage:'@openworkerai/harness-sdk', sdkVersion:pkg.openworkerSdkVersion,
  },null,2)+'\n', {flag:'wx'});
  const current = await existingDirectory(destination);
  if (!!previous !== !!current || (previous && (previous.ino !== current.ino || previous.dev !== current.dev)))
   throw Error('The existing installation changed while preparing. Review it and retry.');
  if (previous) {
   const backupRoot = join(settings, 'skill-backups'); await realDirectory(backupRoot);
   backupContainer = await mkdtemp(join(backupRoot, 'openworker-harness-builder-'));
   backup = join(backupContainer, 'openworker-harness-builder');
   await renamePath(destination, backup); moved = true;
  }
  try { await renamePath(stage, destination); stage = undefined; }
  catch(error) {
   if (moved) {
    try { await renamePath(backup, destination); moved = false; }
    catch { throw Error(`Replacement failed and the previous installation could not be restored automatically. Its backup is preserved at ${backup}. Inspect ${destination} before retrying.`, {cause:error}); }
    throw Error('Replacement failed; the previous installation was restored.', {cause:error});
   }
   throw error;
  }
 } finally {
  if(stage) await rm(stage, {recursive:true,force:true});
  if(backupContainer && !moved) await rmdir(backupContainer);
  await rmdir(lock);
 }
 if(backup) onBackup(backup);
 return destination;
}
export async function runCli(args, {input = stdin, output = stdout, errorOutput = stderr, userHome = homedir()} = {}) {
 if(args.length === 1 && args[0] === '--help') {
  output.write(`${usage}\n--global installs across projects for this user (Codex: ~/.agents/skills; Claude Code: ~/.claude/skills).\nExisting skills prompt in an interactive terminal; --replace-existing skips that prompt.\nReplacements always save a backup outside the active skills directory.\n`);
  return 0;
 }
 try {
  const options = parseInstallArgs(args);
  let backup;
  const confirmReplace = input.isTTY && output.isTTY ? async destination => {
   output.write(`Existing skill: ${destination}\n`);
   const terminal = createInterface({input,output,terminal:false});
   const controller = new AbortController();
   terminal.once('close',()=>controller.abort());
   try {
    const answer = await terminal.question('Replace the existing skill? A backup will be saved. [y/N] ', {signal:controller.signal});
    return /^(y|yes)$/i.test(answer.trim());
   } catch { return false; } // EOF/cancel never authorizes replacement.
   finally { terminal.close(); }
  } : undefined;
  const destination = await install(options, {userHome,confirmReplace,onBackup:path => {backup = path;}});
  if(backup) output.write(`Backup saved: ${backup}\n`);
  output.write(`Installed: ${destination}\nOpen a new coding-agent session${options.global ? '' : ' in that project'} to use openworker-harness-builder.\n`);
  return 0;
 } catch(error) {
  if(error.code === 'INSTALL_CANCELLED') { output.write(error.message+'\n'); return 0; }
  errorOutput.write(error.message+'\n'); return 1;
 }
}
// Canonicalize both sides for Windows directory casing and npm's symlinked bin.
const entryPath = process.argv[1] && await realpath(process.argv[1]).catch(() => resolve(process.argv[1]));
const modulePath = await realpath(fileURLToPath(import.meta.url));
const isMain = entryPath && (process.platform === 'win32'
 ? entryPath.toLowerCase() === modulePath.toLowerCase() : entryPath === modulePath);
if (isMain) process.exitCode = await runCli(process.argv.slice(2));
