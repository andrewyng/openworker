import generated from '../content/docs/desktop.md?raw';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';

// The existing preparation pipeline resolves links/tabs from the authoritative
// guide. This preview changes its presentation, not its prose or SDK examples.
export async function desktopPreview() {
 const title=JSON.parse(generated.match(/^title: (.+)$/m)[1]);
 const markdown=generated.replace(/^---\n[\s\S]*?\n---\n/,'').trim();
 const boundary=markdown.indexOf('\n\n## ');
 const introduction=markdown.slice(0,boundary).replace(/\n/g,' ');
 const sentenceEnd=introduction.indexOf('. ')+1;
 const subtitle=introduction.slice(0,sentenceEnd);
 const body=introduction.slice(sentenceEnd).trim()+'\n\n'+markdown.slice(boundary).trim();
 const processor=await createMarkdownProcessor({syntaxHighlight:'shiki',shikiConfig:{theme:'github-light'},gfm:true});
 const rendered=await processor.render(body);
 const lead=await processor.render(subtitle);
 const html=rendered.code.replace(/(<pre\b[\s\S]*?<\/pre>)/g,'<div class="code-sample"><button class="code-copy" type="button" aria-label="Copy code">Copy</button>$1</div>');
 return {title,subtitle:lead.code,html,headings:rendered.metadata.headings.filter(h=>h.depth===2)};
}

export {navigation} from './navigation.mjs';
