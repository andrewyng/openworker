// One authoritative viewer implementation, copied into the standalone UI image.
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
for(const file of ['observability.js','observability-data.js']) {
 const source=await readFile(new URL(`src/${file}`,import.meta.url),'utf8');
 const target=new URL(`../service-ui/public/${file}`,import.meta.url);
 if(process.argv.includes('--check'))assert.equal(await readFile(target,'utf8'),source,`${file}: run npm run sync in trace-viewer`);
 else await writeFile(target,source);
}
