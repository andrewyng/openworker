#!/usr/bin/env node
// Local artifact preparation only. Never publishes or downloads executables.
import {readFile,writeFile,mkdir,cp,chmod,stat} from 'node:fs/promises';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {runtimeArch} from '../desktop-starters/templates/shared/scripts/runtime-identity.mjs';
import {prepare as prepareSkills} from '../../skills/prepare.mjs';
const here=dirname(fileURLToPath(import.meta.url)),root=resolve(here,'..');
const manifest=JSON.parse(await readFile(join(here,'manifest.json'),'utf8'));
const [binary,targetName,output]=process.argv.slice(2);
const target=manifest.targets.find(t=>t.rust===targetName);
if(!binary||!target||!output)throw Error('Usage: node harness/release/prepare.mjs ABS_BINARY RUST_TARGET NEW_OUTPUT_DIRECTORY');
const out=resolve(output);await mkdir(out); // Refuse to overwrite a prepared release.
const bytes=await readFile(resolve(binary));
if(runtimeArch(bytes,target.platform)!==target.arch)throw Error('Binary architecture does not match release target');
if(target.platform===process.platform&&target.arch===process.arch) {
 const version=execFileSync(resolve(binary),['--version'],{encoding:'utf8'}).trim();
 if(!version.endsWith(' '+manifest.version))throw Error('Binary release version mismatch');
}
const hash=createHash('sha256').update(bytes).digest('hex');
const filename=target.platform==='win32'?'openworker-harness.exe':'openworker-harness';
const name=`@openworkerai/harness-runtime-${target.platform}-${target.arch}`;
const native=join(out,'native');await mkdir(join(native,'bin'),{recursive:true});
await cp(resolve(binary),join(native,'bin',filename),{dereference:true});await chmod(join(native,'bin',filename),0o755);
const metadata={version:manifest.version,protocol:manifest.protocol,platform:target.platform,arch:target.arch,target:target.rust,sha256:hash};
await writeFile(join(native,'runtime.json'),JSON.stringify(metadata,null,2)+'\n');
await writeFile(join(native,'package.json'),JSON.stringify({name,version:manifest.version,description:'OpenWorker native harness runtime',license:'MIT',os:[target.platform],cpu:[target.arch],...(target.platform==='linux'?{libc:['glibc']}:{}),files:['bin','runtime.json','LICENSE','README.md'],repository:{type:'git',url:'git+https://github.com/andrewyng/openworker.git'},publishConfig:{access:'public',tag:'beta'}},null,2)+'\n');
await cp(join(root,'..','LICENSE'),join(native,'LICENSE'));
await writeFile(join(native,'README.md'),`# ${name}\n\nMatching native host for @openworkerai/harness-sdk ${manifest.version}. Installed automatically for this platform.\n`);
for(const [directory,files] of [['typescript-sdk',['dist','examples','README.md','LICENSE']],['desktop-starters',['create.mjs','templates','README.md']]]) {
 const dest=join(out,directory);await mkdir(dest);
 const pkg=JSON.parse(await readFile(join(root,directory,'package.json'),'utf8'));
 if(pkg.version!==manifest.version)throw Error('Release version mismatch');
 delete pkg.private;delete pkg.scripts;delete pkg.devDependencies;
 if(directory==='typescript-sdk')pkg.optionalDependencies=Object.fromEntries(manifest.targets.map(t=>[`@openworkerai/harness-runtime-${t.platform}-${t.arch}`,manifest.version]));
 for(const file of files)await cp(join(root,directory,file),join(dest,file),{recursive:true});
 await cp(join(root,'..','LICENSE'),join(dest,'LICENSE'));
 await writeFile(join(dest,'package.json'),JSON.stringify(pkg,null,2)+'\n');
}
const archives=join(out,'archives');await mkdir(archives);
const npm=process.platform==='win32'?'npm.cmd':'npm';
for(const directory of ['native','typescript-sdk','desktop-starters'])execFileSync(npm,['pack','--ignore-scripts','--json','--pack-destination',archives],{cwd:join(out,directory),stdio:'inherit',shell:process.platform==='win32'});
const skillArchives=await prepareSkills(join(out,'skills'));
await cp(join(skillArchives,`openworkerai-build-with-sdk-skills-${manifest.version}.tgz`),join(archives,`openworkerai-build-with-sdk-skills-${manifest.version}.tgz`));
// Portable GitHub archive, with the same executable and metadata as npm.
const archive=`openworker-harness-${manifest.version}-${target.rust}.tar.gz`;
// A Windows drive letter is parsed as a remote host by GNU tar. Keep both
// archive and input paths relative to the controlled staging directory.
execFileSync('tar',['-czf',`../archives/${archive}`,'bin','runtime.json','LICENSE','README.md'],{cwd:native});
const {readdir}=await import('node:fs/promises');const sums=[];
for(const file of (await readdir(archives)).sort())sums.push(`${createHash('sha256').update(await readFile(join(archives,file))).digest('hex')}  ${file}`);
await writeFile(join(archives,'SHA256SUMS'),sums.join('\n')+'\n');
const commit=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
const dirty=!!execFileSync('git',['status','--porcelain','--untracked-files=no'],{cwd:root,encoding:'utf8'}).trim();
await writeFile(join(archives,'release.json'),JSON.stringify({...metadata,commit,dirty,publication:'not-published'},null,2)+'\n');
console.log(`Prepared ${archives}. Registry publication and platform acceptance are separate.`);
