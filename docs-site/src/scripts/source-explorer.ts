import {sourceTree,visibleSourceRows,sourceLanguage} from '../lib/source-tree.mjs';
type SourceFile={path:string;content:string};
const drawer=document.querySelector<HTMLDialogElement>('#example-source-drawer');
if(drawer){
 const tree=drawer.querySelector<HTMLElement>('[data-source-files]')!;
 const breadcrumb=drawer.querySelector<HTMLElement>('[data-source-path]')!;
 const host=drawer.querySelector<HTMLElement>('[data-source-editor]')!;
 const fallback=drawer.querySelector<HTMLElement>('.source-fallback')!;
 const content=drawer.querySelector<HTMLElement>('[data-source-content]')!;
 const status=drawer.querySelector<HTMLElement>('[data-source-status]')!;
 const meta=drawer.querySelector<HTMLElement>('[data-source-meta]')!;
 const copy=drawer.querySelector<HTMLButtonElement>('[data-source-copy]')!;
 const find=drawer.querySelector<HTMLButtonElement>('[data-source-find]')!;
 const wrap=drawer.querySelector<HTMLButtonElement>('[data-source-wrap]')!;
 const filter=drawer.querySelector<HTMLInputElement>('.source-filter input')!;
 let request:AbortController|undefined,epoch=0,selection=0,current:SourceFile|undefined;
 let root:ReturnType<typeof sourceTree>|undefined,focusPath='',expanded=new Set<string>();
 let editor:ReturnType<typeof import('./source-editor').createSourceEditor>|undefined;
 function icon(kind:'folder'|'chevron'){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 20 20');svg.setAttribute('aria-hidden','true');svg.classList.add('tree-'+kind);
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',kind==='folder'?'M2.5 5.5h6l2 2h7v8.5h-15z':'m7.5 5 5 5-5 5');svg.append(path);return svg;
 }
 async function select(file:SourceFile){
  current=file;const token=++selection;breadcrumb.replaceChildren();
  file.path.split('/').forEach((part,index)=>{if(index){const slash=document.createElement('span');slash.textContent='/';slash.className='source-slash';breadcrumb.append(slash);}const label=document.createElement('span');label.textContent=part;breadcrumb.append(label);});
  const language=sourceLanguage(file.path);const lines=file.content.split('\n').length;
  meta.textContent=`${language.label} · ${lines.toLocaleString()} lines`;
  copy.disabled=true;find.disabled=true;wrap.disabled=true;host.setAttribute('aria-busy','true');
  tree.querySelectorAll('[role=treeitem]').forEach(item=>item.setAttribute('aria-selected',String((item as HTMLElement).dataset.path===file.path)));
  try{
   if(editor){const applied=await editor.show(file);if(!applied||token!==selection)return;fallback.hidden=true;host.hidden=false;find.disabled=false;wrap.disabled=false;status.textContent='';}
   else{content.textContent=file.content;fallback.hidden=false;host.hidden=true;status.textContent='Showing plain source. The code viewer could not load.';}
  }catch{if(token!==selection)return;content.textContent=file.content;fallback.hidden=false;host.hidden=true;status.textContent='Showing plain source. Syntax highlighting is unavailable.';}
  if(token===selection){copy.disabled=false;host.removeAttribute('aria-busy');}
 }
 function render(focus=false){
  if(!root)return;
  const rows=visibleSourceRows(root,expanded,filter.value);tree.replaceChildren();
  drawer!.querySelector<HTMLElement>('.source-no-files')!.hidden=rows.length>0;
  if(!rows.some(row=>row.node.path===focusPath))focusPath=rows.find(row=>row.node.path===current?.path)?.node.path??rows[0]?.node.path??'';
  rows.forEach((row,index)=>{
   const {node}=row;const button=document.createElement('button');button.type='button';button.className='source-tree-row';button.setAttribute('role','treeitem');
   button.setAttribute('aria-label',node.path);button.setAttribute('aria-level',String(row.depth));button.setAttribute('aria-posinset',String(row.position));button.setAttribute('aria-setsize',String(row.size));
   button.setAttribute('aria-selected',String(node.path===current?.path));button.tabIndex=node.path===focusPath?0:-1;button.dataset.path=node.path;button.style.setProperty('--depth',String(row.depth-1));
   if(node.kind==='folder'){button.setAttribute('aria-expanded',String(row.open));button.append(icon('chevron'),icon('folder'));}
   else{const spacer=document.createElement('span');spacer.className='tree-spacer';const badge=document.createElement('span');badge.className=`file-icon file-${sourceLanguage(node.path).id}`;badge.textContent=sourceLanguage(node.path).icon;badge.setAttribute('aria-hidden','true');button.append(spacer,badge);}
   const name=document.createElement('span');name.className='tree-name';name.textContent=node.name;button.append(name);
   button.addEventListener('focus',()=>{focusPath=node.path;tree.querySelectorAll<HTMLButtonElement>('[role=treeitem]').forEach(item=>item.tabIndex=item===button?0:-1);});
   button.addEventListener('click',()=>{focusPath=node.path;if(node.kind==='folder'){expanded.has(node.path)?expanded.delete(node.path):expanded.add(node.path);render(true);}else void select(node.file);});
   button.addEventListener('keydown',event=>{
    let target:string|undefined;
    if(event.key==='ArrowDown')target=rows[Math.min(index+1,rows.length-1)]?.node.path;
    else if(event.key==='ArrowUp')target=rows[Math.max(index-1,0)]?.node.path;
    else if(event.key==='Home')target=rows[0]?.node.path;
    else if(event.key==='End')target=rows.at(-1)?.node.path;
    else if(event.key==='ArrowRight'&&node.kind==='folder'){if(!row.open){expanded.add(node.path);render(true);}else target=rows[index+1]?.node.path;}
    else if(event.key==='ArrowLeft'){if(node.kind==='folder'&&row.open&&!filter.value){expanded.delete(node.path);render(true);}else target=node.path.includes('/')?node.path.slice(0,node.path.lastIndexOf('/')):undefined;}
    else return;
    event.preventDefault();if(target){focusPath=target;[...tree.querySelectorAll<HTMLButtonElement>('[role=treeitem]')].find(item=>item.dataset.path===target)?.focus();}
   });
   tree.append(button);
  });
  if(focus)[...tree.querySelectorAll<HTMLButtonElement>('[role=treeitem]')].find(item=>item.dataset.path===focusPath)?.focus();
 }
 filter.addEventListener('input',()=>render());
 drawer.querySelector('[data-source-close]')!.addEventListener('click',()=>drawer.close());
 drawer.addEventListener('close',()=>{request?.abort();epoch++;selection++;editor?.destroy();editor=undefined;host.replaceChildren();});
 drawer.addEventListener('click',event=>{if(event.target===drawer){const rect=drawer.getBoundingClientRect(),mouse=event as MouseEvent;if(mouse.clientX<rect.left||mouse.clientX>rect.right||mouse.clientY<rect.top||mouse.clientY>rect.bottom)drawer.close();}});
 copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(current?.content??'');copy.textContent='Copied';setTimeout(()=>{copy.textContent='Copy';},1500);}catch{status.textContent='Select the code to copy it.';}});
 find.addEventListener('click',()=>editor?.search());
 wrap.addEventListener('click',()=>{const enabled=wrap.getAttribute('aria-pressed')!=='true';wrap.setAttribute('aria-pressed',String(enabled));editor?.setWrap(enabled);});
 for(const button of document.querySelectorAll<HTMLButtonElement>('[data-example-source]'))button.addEventListener('click',async()=>{
  request?.abort();request=new AbortController();const token=++epoch;
  tree.replaceChildren();breadcrumb.replaceChildren();meta.textContent='';current=undefined;root=undefined;filter.value='';copy.disabled=true;find.disabled=true;wrap.disabled=true;fallback.hidden=true;host.hidden=false;host.replaceChildren();
  expanded=new Set(['src','desktop','web']);wrap.setAttribute('aria-pressed','true');status.textContent='Loading example…';drawer.showModal();
  try{
   const [response,module]=await Promise.all([fetch(button.dataset.exampleSource!,{signal:request.signal}),import('./source-editor').catch(()=>null)]);
   if(!response.ok)throw Error('Source unavailable');const data:{files:SourceFile[]}=await response.json();
   if(token!==epoch||!drawer.open)return;
   root=sourceTree(data.files);drawer.querySelector<HTMLElement>('[data-source-count]')!.textContent=String(data.files.length);
   if(module)editor=module.createSourceEditor(host);
   const initial=data.files.find(file=>file.path==='src/agent.ts')??data.files[0];
   if(!initial)throw Error('No source files');focusPath=initial.path;current=initial;render();await select(initial);
  }catch(error){if(token===epoch&&!(error instanceof DOMException&&error.name==='AbortError'))status.textContent='Unable to load source. The complete example is linked in the guide.';}
 });
}
