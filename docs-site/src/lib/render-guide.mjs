import { createMarkdownProcessor } from '@astrojs/markdown-remark';
const processor = createMarkdownProcessor({syntaxHighlight:'shiki',shikiConfig:{theme:'github-light'},gfm:true});

export async function renderGuide(generated) {
 const title=JSON.parse(generated.match(/^title: (.+)$/m)[1]);
 const markdown=generated.replace(/^---\n[\s\S]*?\n---\n/,'').trim();
 let subtitle='',body=markdown;
 // Move only the first prose sentence into the hero. Keep tables, fences and
 // all remaining content intact, including guides with no opening paragraph.
 if(markdown && !/^[|#`<>*\-]/.test(markdown)) {
  const end=markdown.search(/\n\s*\n/);
  const paragraph=(end<0?markdown:markdown.slice(0,end)).replace(/\n/g,' ');
  const stop=paragraph.indexOf('. ');
  subtitle=stop<0?paragraph:paragraph.slice(0,stop+1);
  const remaining=stop<0?'':paragraph.slice(stop+1).trim();
  body=remaining+'\n\n'+(end<0?'':markdown.slice(end).trim());
 }
 const renderer=await processor;
 const [article,lead]=await Promise.all([renderer.render(body),renderer.render(subtitle)]);
 const html=article.code.replace(/(<pre\b[\s\S]*?<\/pre>)/g,'<div class="code-sample"><button class="code-copy" type="button" aria-label="Copy code" data-pagefind-ignore>Copy</button>$1</div>');
 return {title,subtitle:lead.code,html,headings:article.metadata.headings.filter(h=>h.depth===2)};
}
