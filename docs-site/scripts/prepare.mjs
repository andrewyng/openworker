// Plain Markdown in the repo is authoritative. Generated frontmatter/links are site-only.
import {readFileSync,writeFileSync,mkdirSync,rmSync,existsSync} from 'node:fs';
import {dirname,resolve,join,posix} from 'node:path';
import {fileURLToPath} from 'node:url';
const site=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(site,'..');
const pages=JSON.parse(readFileSync(join(site,'pages.json'),'utf8'));
const output=join(site,'src/content/docs'),base=(process.env.OW_DOCS_BASE??'/docs/sdk').replace(/\/$/,'');
const route=new Map(pages.map(p=>[p.source,`${base}/${p.slug==='index'?'':p.slug+'/'}`]));
rmSync(output,{recursive:true,force:true});mkdirSync(output,{recursive:true});
for(const page of pages) {
 let raw=readFileSync(join(root,page.source),'utf8');const title=raw.match(/^# (.+)$/m);
 if(!title)throw Error(`Missing H1: ${page.source}`);
 raw=raw.replace(title[0],'').trimStart();
 raw=raw.replace(/\]\(([^\s)]+)\)/g,(full,target)=>{
  if(/^(?:[a-z]+:|\/|#)/i.test(target))return full;
  const [file,anchor]=target.split('#');const absolute=posix.normalize(posix.join(posix.dirname(page.source),file));
  if(!existsSync(join(root,absolute)))throw Error(`Broken link ${page.source} -> ${target}`);
  return `](${route.get(absolute)??'https://github.com/andrewyng/openworker/blob/openworker-harness-sdk/'+absolute}${anchor?'#'+anchor:''})`;
 });
 writeFileSync(join(output,page.slug+'.md'),`---\ntitle: ${JSON.stringify(title[1])}\n---\n\n${raw}`);
}
console.log(`Prepared ${pages.length} SDK pages from repository Markdown`);
