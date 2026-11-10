import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
function files(dir) {
  return readdirSync(dir,{withFileTypes:true}).flatMap(e=>{
    if(['target','node_modules','build','dist','release','.git'].includes(e.name)) return [];
    const p=join(dir,e.name);return e.isDirectory()?files(p):e.isFile()?[p]:[];
  });
}
test('workspace members exist and legacy apps are not release dependencies',()=>{
  const cargo=readFileSync(join(root,'harness/Cargo.toml'),'utf8');
  const members=cargo.match(/members\s*=\s*\[([^\]]+)\]/)[1].match(/"([^"]+)"/g).map(s=>s.slice(1,-1));
  for(const member of members) assert.ok(existsSync(join(root,'harness',member,'Cargo.toml')),member);
  for(const app of ['pr-review','personal-assistant','lantern-runtime','luma-runtime']) assert.equal(existsSync(join(root,'harness/examples',app)),false,app);
  assert.ok(members.includes('examples/notification-tools'));
  assert.ok(members.includes('examples/device-tools'));
  assert.ok(members.includes('examples/computer-tools'));
  const fixture=readFileSync(join(root,'harness/runtime/src/compaction.rs'),'utf8');
  assert.ok(fixture.includes('contracts/compaction.json'));
});
test('published Markdown documentation has no dangling relative links',()=>{
  const missing=[];
  for(const p of ['harness','docs/harness','deploy'].flatMap(d=>files(join(root,d))).filter(p=>p.endsWith('.md'))) {
    const s=readFileSync(p,'utf8');
    for(const match of s.matchAll(/\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)/g)) {
      const link=match[1].split('#')[0];
      if(!link||/^[a-z]+:|^\//i.test(link)||link.includes('<')) continue;
      if(!existsSync(resolve(dirname(p),link))) missing.push(`${relative(root,p)} -> ${link}`);
    }
  }
  assert.deepEqual(missing,[]);
});
test('public quickstart is self-contained and states host execution boundaries',()=>{
  const guide=readFileSync(join(root,'docs/harness/guides/desktop.md'),'utf8');
  assert.ok(guide.includes('harness/examples/quickstart'));
  assert.ok(guide.includes('sandbox: "off"'));
  assert.ok(guide.includes('not an OS-isolation demonstration'));
  assert.ok(existsSync(join(root,'docs/harness/guides/server.md')));
  assert.equal(existsSync(join(root,'docs/harness/evidence')),false);
  const top=readdirSync(join(root,'docs/harness'));
  assert.deepEqual(top,['guides']);
});
