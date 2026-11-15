import test from 'node:test';
import assert from 'node:assert/strict';
import {renderGuide} from '../src/lib/render-guide.mjs';
const wrap=body=>'---\ntitle: "Guide"\n---\n\n'+body;
test('promotes introductory sentence without losing remaining prose or code',async()=>{
 const result=await renderGuide(wrap('First sentence. Second sentence.\n\n## Work\n\n```ts\nconst value = 42;\n```'));
 assert.match(result.subtitle,/First sentence\./);
 assert.match(result.html,/Second sentence\./);
 assert.equal(result.headings[0].slug,'work');
 assert.match(result.html.replace(/<[^>]*>/g,''),/const value = 42;/);
});
test('table-first and single-paragraph guides preserve all content',async()=>{
 const table=await renderGuide(wrap('| Symptom | Check |\n|---|---|\n| Missing runtime | Set a path |'));
 assert.equal(table.subtitle,'');assert.match(table.html,/<table>/);assert.match(table.html,/Missing runtime/);
 const single=await renderGuide(wrap('Use version `0.1.0-beta.1`. Keep the helper matched.'));
 assert.match(single.subtitle,/0\.1\.0-beta\.1/);assert.match(single.html,/Keep the helper matched/);
});
