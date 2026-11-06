import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync,mkdtempSync,rmSync} from 'node:fs';
import {dirname,resolve,join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const manifest=JSON.parse(readFileSync(join(root,'release/manifest.json')));
test('release versions and publishable Rust dependency closure agree',()=>{
 assert.match(manifest.version,/^\d+\.\d+\.\d+-beta\.\d+$/);
 assert.ok(readFileSync(join(root,'Cargo.toml'),'utf8').includes(`version = "${manifest.version}"`));
 for(const pkg of manifest.rustPackages) {
  const cargo=readFileSync(join(root,pkg.directory,'Cargo.toml'),'utf8');
  assert.ok(cargo.includes('version.workspace = true'));assert.ok(cargo.includes('publish = ["crates-io"]'));
  for(const line of cargo.split('\n').filter(l=>l.includes('{')&&l.includes('path =')))assert.ok(line.includes(`version = "=${manifest.version}"`),line);
  assert.ok(existsSync(join(root,pkg.directory,'LICENSE')));
 }
 for(const folder of ['typescript-sdk','desktop-starters'])assert.equal(JSON.parse(readFileSync(join(root,folder,'package.json'))).version,manifest.version);
 assert.equal(JSON.parse(readFileSync(join(root,'../skills/package.json'))).version,manifest.version);
 assert.ok(readFileSync(join(root,'typescript-sdk/src/version.ts'),'utf8').includes(JSON.stringify(manifest.version)));
 assert.equal(new Set(manifest.targets.map(t=>t.rust)).size,manifest.targets.length);
});
test('source-mode starter keeps explicit checkout references',()=>{
 const dir=mkdtempSync(join(tmpdir(),'ow-source-starter-'));
 try {
 const result=spawnSync(process.execPath,[join(root,'desktop-starters/create.mjs'),join(dir,'app'),'--sdk-root',root],{encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);
 assert.equal(JSON.parse(readFileSync(join(dir,'app/package.json'))).dependencies['@openworkerai/harness-sdk'],'file:'+join(root,'typescript-sdk'));
 }finally{rmSync(dir,{recursive:true,force:true});}
});
test('registry-mode starters require no checkout paths',()=>{
 const dir=mkdtempSync(join(tmpdir(),'ow-registry-starter-'));
 try {for(const template of ['electron-game-builder','tauri-game-builder']) {
 const dest=join(dir,template);const result=spawnSync(process.execPath,[join(root,'desktop-starters/create.mjs'),dest,'--template',template],{encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);const config=JSON.parse(readFileSync(join(dest,'openworker.json')));assert.equal(config.sdkRoot,null);
 const pkg=JSON.parse(readFileSync(join(dest,'package.json')));assert.equal((pkg.dependencies['@openworkerai/harness-sdk']??pkg.devDependencies['@openworkerai/harness-sdk']),manifest.version);
 if(template.startsWith('tauri'))assert.ok(readFileSync(join(dest,'src-tauri/Cargo.toml'),'utf8').includes(`openworker-sdk = "=${manifest.version}"`));
 }}finally{rmSync(dir,{recursive:true,force:true});}
});
