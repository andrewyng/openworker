import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {renderCodeTabs} from './code-tabs.mjs';
const desktop=readFileSync(fileURLToPath(new URL('../../docs/harness/guides/desktop.md',import.meta.url)),'utf8');
const snippets=s=>[...s.matchAll(/^```[^\n]*\n([\s\S]*?)^```/gm)].map(m=>m[1]);
test('guide tabs retain all code and provide unique linked accessible panels',()=>{
 const result=renderCodeTabs(desktop,'desktop');
 assert.deepEqual(snippets(result),snippets(desktop));
 assert.equal((result.match(/data-sdk-tabs/g)||[]).length,6);
 assert.equal((result.match(/role="tablist"/g)||[]).length,6);
 const ids=[...result.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
 assert.equal(ids.length,24);assert.equal(new Set(ids).size,ids.length);
 for(const [,target]of result.matchAll(/(?:aria-controls|aria-labelledby)="([^"]+)"/g))assert.ok(ids.includes(target));
 assert.equal((result.match(/class="sdk-tab-fallback"/g)||[]).length,12);
 assert.equal((result.match(/role="tabpanel"[^>]*\bhidden/g)||[]).length,0);
 assert.ok(!result.includes('<!-- sdk-tabs -->'));
 assert.ok(result.includes('data-tab-kind="installer"'));
 assert.ok(result.includes('data-tab-kind="language"'));
 assert.ok(result.includes('aria-label="Coding agent"'));
});
test('malformed language markers stop the build instead of hiding content',()=>{
 for(const source of [
  '<!-- sdk-tabs -->\n### TypeScript\nhello',
  '<!-- sdk-tabs -->\n### TypeScript\none\n### TypeScript\ntwo\n<!-- /sdk-tabs -->',
  '<!-- sdk-tabs -->\n### Python\none\n### Rust\ntwo\n<!-- /sdk-tabs -->',
  '<!-- /sdk-tabs -->',
  '<!-- sdk-install-tabs -->\n### Codex\nhello',
  '<!-- sdk-install-tabs -->\n### TypeScript\none\n### Rust\ntwo\n<!-- /sdk-install-tabs -->',
 ])assert.throws(()=>renderCodeTabs(source,'broken'));
});
