#!/usr/bin/env node
// Extract the exact fenced programs from the Markdown; never maintain a second copy.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const output=resolve(process.argv[2]??'.sdk-release-examples');await mkdir(output,{recursive:true});
// Windows checkouts may use CRLF; code fences have the same meaning there.
const source=(await readFile(join(root,'docs/harness/guides/desktop.md'),'utf8')).replace(/\r\n/g,'\n');
for(const [language,file]of [['typescript','desktop.ts'],['rust','desktop.rs']]) {
 const snippets=[...source.matchAll(new RegExp('```'+language+' example=desktop\\n([\\s\\S]*?)```','g'))].map(m=>m[1]);
 if(snippets.length!==1)throw Error(`Expected one complete ${language} program`);
 await writeFile(join(output,file),snippets.join('\n'));
}
console.log(output);
