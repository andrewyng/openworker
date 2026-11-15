import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {docsProduct} from '../product.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
test('Desktop and SDK have explicit separate manifests and navigation',()=>{
 const sdk=docsProduct({}),desktop=docsProduct({OW_DOCS_PRODUCT:'desktop'});
 assert.equal(sdk.base,'/docs/sdk');
 assert.equal(desktop.base,'/docs/desktop');
 assert.notEqual(sdk.content,desktop.content);
 const required=['index','first-task','how-it-works','models','local-models','voice','coworkers','coding','security','machines','run-location','sandbox','native-sandbox','openshell','approvals','privacy','troubleshooting'];
 for(const slug of required)assert.ok(desktop.pages.some(page=>page.slug===slug),slug);
 for(const product of [sdk,desktop]) {
  const slugs=new Set(product.pages.map(page=>page.slug==='index'?'':page.slug));
  assert.equal(slugs.size,product.pages.length,'Duplicate page routes');
  for(const section of product.navigation)for(const [,slug]of section.links)assert.ok(slugs.has(slug),slug);
  for(const page of product.pages) {
   assert.ok(page.source.startsWith(`docs/${product.id==='sdk'?'harness':'desktop'}/guides/`));
   assert.ok(existsSync(resolve(root,page.source)));
   assert.ok(!/ledger|journal|evidence|\.env/i.test(page.source));
  }
 }
 assert.equal(docsProduct({OW_DOCS_PRODUCT:'desktop',OW_DOCS_BASE:'/preview/desktop'}).base,'/preview/desktop');
 assert.throws(()=>docsProduct({OW_DOCS_PRODUCT:'unknown'}),/Unknown docs product/);
});

test('first-task sample and UI instructions agree with the app source',()=>{
 const read=path=>readFileSync(resolve(root,path),'utf8');
 const ui=JSON.parse(read('surfaces/gui/src/locales/en.json'));
 const guide=read('docs/desktop/guides/first-task.md');
 for(const label of [ui.intro.task_folder_title,ui.access.allow_writes,ui.composer.mode.interactive,ui.nav.settings])assert.ok(guide.includes(label),label);
 const samples=['01-kickoff.md','02-design-review.md','03-launch-check.md'].map(name=>read(`docs/desktop/samples/meeting-notes/${name}`)).join('\n');
 for(const phrase of ['read-only preview','Priya','Thursday','Sam','Friday','support handoff','not confirmed'])assert.ok(samples.includes(phrase),phrase);
 assert.ok(guide.includes('../samples/meeting-notes.zip'));
 assert.ok(!/0\.\d+\.\d+|beta\.\d+/i.test(guide),'Keep release numbers out of the walkthrough');
});
