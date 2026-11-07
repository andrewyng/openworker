import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import vm from 'node:vm';

test('review artifact excludes previews, marks HTML noindex and routes only published docs', async () => {
  const root = await mkdtemp(join(tmpdir(), 'sdk-hosting-'));
  try {
    const input = join(root, 'dist'), output = join(root, 'release');
    for (const path of ['index.html', 'desktop/index.html', 'previews/desktop/apple/index.html', '_astro/editor.js', 'sitemap-0.xml', 'reference/architecture.html']) {
      const file = join(input, path);
      await mkdir(join(file, '..'), {recursive: true});
      await writeFile(file, path.endsWith('.html') ? '<html><head></head><body>Guide</body></html>' : 'asset');
    }
    execFileSync(process.execPath, [new URL('./prepare-hosting.mjs', import.meta.url).pathname, input, output, 'a'.repeat(40)]);
    const manifest = JSON.parse(await readFile(join(output, 'manifest.json')));
    assert.equal(manifest.files.length, 4);
    for (const file of manifest.files.filter(f => f.path.endsWith('.html'))) assert.match(await readFile(join(output, 'files', file.path), 'utf8'), /noindex, nofollow/);
    const context = vm.createContext({});
    vm.runInContext(await readFile(join(output, 'router.js'), 'utf8'), context);
    const route = uri => context.handler({request: {uri, querystring: {q: {value: 'hello'}}}});
    for (const uri of ['/docs/sdk', '/docs/sdk/']) assert.equal(route(uri).uri, '/docs/sdk/index.html');
    for (const uri of ['/docs/sdk/desktop', '/docs/sdk/desktop/']) assert.equal(route(uri).uri, '/docs/sdk/desktop/index.html');
    assert.equal(route('/docs/sdk/_astro/editor.js').uri, '/docs/sdk/_astro/editor.js');
    assert.equal(route('/docs/sdk/reference/architecture.html').uri, '/docs/sdk/reference/architecture.html');
    assert.equal(route('/docs/sdk/desktop').querystring.q.value, 'hello');
    for (const uri of ['/docs/sdk/previews/desktop/apple/', '/docs/sdk/sitemap-0.xml', '/docs/sdk/missing/', '/docs/sdk/_astro/missing.js', '/docs/sdk/../index.html', '/docs/sdk/toString', '/docs/sdk/__proto__']) assert.equal(route(uri).statusCode, 404);
    for (const uri of ['/', '/account', '/resources/post', '/docs/sdk-other']) assert.equal(route(uri).uri, uri);
  } finally { await rm(root, {recursive: true, force: true}); }
});
