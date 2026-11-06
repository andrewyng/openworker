import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const site=resolve(dirname(fileURLToPath(import.meta.url)),'..');
test('public Markdown builds default/versioned routes and removes stale private references',()=>{
 const pages=JSON.parse(readFileSync(join(site,'pages.json'),'utf8'));
 const originals=pages.map(p=>readFileSync(join(site,'..',p.source),'utf8'));
 mkdirSync(join(site,'public/reference'),{recursive:true});
 writeFileSync(join(site,'public/reference/service-architecture.html'),'stale specification');
 try {for(const base of ['/docs/sdk','/docs/sdk/0.1.0-beta.1']) {
  execFileSync(process.execPath,[join(site,'scripts/prepare.mjs')],{env:{...process.env,OW_DOCS_BASE:base}});
  for(const p of pages) {
   const text=readFileSync(join(site,'src/content/docs',p.slug+'.md'),'utf8');
   assert.ok(text.startsWith('---\ntitle:'));assert.ok(!/^# /m.test(text.replace(/```[\s\S]*?```/g,'')));
   assert.ok(!/\]\([^\s)]+\.md(?:#[^)]*)?\)/.test(text.replace(/https:\/\/[^)\s]+/g,'')));
  }
  assert.ok(readFileSync(join(site,'src/content/docs/index.md'),'utf8').includes(base+'/desktop/'));
  for(const [page,image] of [['desktop','code-atlas'],['server','pdf-notes']]) {
   assert.ok(readFileSync(join(site,'src/content/docs',page+'.md'),'utf8').includes(base+'/examples/'+image+'.png'));
   assert.deepEqual(readFileSync(join(site,'public/examples',image+'.png')),readFileSync(join(site,'../docs/harness/guides/images',image+'.png')));
  }
  assert.ok(readFileSync(join(site,'src/content/docs/server.md'),'utf8').includes(base+'/deployment/'));
  assert.equal(existsSync(join(site,'public/reference')),false);
  for(const page of pages) assert.ok(page.source.startsWith('docs/harness/guides/'));
 }} finally {execFileSync(process.execPath,[join(site,'scripts/prepare.mjs')]);}
 pages.forEach((p,i)=>assert.equal(readFileSync(join(site,'..',p.source),'utf8'),originals[i]));
});
