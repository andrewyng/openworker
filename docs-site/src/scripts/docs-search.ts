type SearchHit={url:string;meta:{title?:string};excerpt:string};
type SearchIndex={options:(options:{baseUrl:string})=>Promise<void>;search:(query:string)=>Promise<{results:{data:()=>Promise<SearchHit>}[]}>};
const dialog=document.querySelector<HTMLDialogElement>('#docs-search');
if(dialog){
 const input=dialog.querySelector<HTMLInputElement>('input')!;
 const status=dialog.querySelector<HTMLElement>('.search-status')!;
 const results=dialog.querySelector<HTMLElement>('.search-results')!;
 const base=dialog.dataset.searchBase!;
 let index:Promise<SearchIndex>|undefined,timer:ReturnType<typeof setTimeout>,generation=0;
 function open(){dialog!.showModal();input.focus();if(input.value.trim())input.dispatchEvent(new Event('input'));}
 for(const button of document.querySelectorAll('[data-open-search]'))button.addEventListener('click',open);
 dialog.querySelector('[data-close-search]')!.addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{generation++;clearTimeout(timer);});
 document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();if(!dialog.open)open();}});
 input.addEventListener('input',()=>{
  clearTimeout(timer);const current=++generation,query=input.value.trim();results.replaceChildren();
  if(!query){status.textContent='Search guides, examples and deployment instructions.';return;}
  status.textContent='Searching…';
  timer=setTimeout(async()=>{
   try{
    const url=`${base}/pagefind/pagefind.js`;
    index??=import(/* @vite-ignore */ url).then(async module=>{await module.options({baseUrl:base+'/'});return module as SearchIndex;}).catch(error=>{index=undefined;throw error;});
    const response=await(await index).search(query);
    const hits=await Promise.all(response.results.slice(0,12).map(result=>result.data()));
    if(current!==generation||!dialog.open)return;
    status.textContent=hits.length?`${response.results.length} matching ${response.results.length===1?'page':'pages'}`:'No results. Try a model, capability or deployment name.';
    for(const hit of hits){
     const url=new URL(hit.url,location.origin);
     if(url.origin!==location.origin||!url.pathname.startsWith(base+'/'))continue;
     const link=document.createElement('a');link.href=url.href;
     const title=document.createElement('strong');title.textContent=hit.meta.title??'SDK guide';
     const excerpt=document.createElement('span');excerpt.textContent=new DOMParser().parseFromString(hit.excerpt,'text/html').body.textContent;
     link.append(title,excerpt);results.append(link);
    }
   }catch{if(current===generation)status.textContent='Search is unavailable. Use the guide navigation, or build the site to generate its search index.';}
  },160);
 });
}
const toggle=document.querySelector<HTMLButtonElement>('.mobile-nav-toggle');
toggle?.addEventListener('click',()=>{const expanded=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(expanded));document.body.classList.toggle('navigation-open',expanded);});
