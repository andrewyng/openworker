import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const site=resolve(dirname(fileURLToPath(import.meta.url)),'..');
test('one Markdown source builds default/versioned routes and standalone architecture',()=>{
 const pages=JSON.parse(readFileSync(join(site,'pages.json'),'utf8'));
 const originals=pages.map(p=>readFileSync(join(site,'..',p.source),'utf8'));
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
  assert.ok(readFileSync(join(site,'src/content/docs/server.md'),'utf8').includes(base+'/reference/service-architecture.html'));
  assert.ok(readFileSync(join(site,'public/reference/service-architecture.html'),'utf8').includes('https://github.com/andrewyng/openworker'));
 }} finally {execFileSync(process.execPath,[join(site,'scripts/prepare.mjs')]);}
 pages.forEach((p,i)=>assert.equal(readFileSync(join(site,'..',p.source),'utf8'),originals[i]));
});
