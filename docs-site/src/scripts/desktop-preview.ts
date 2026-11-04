// Shared behaviors; preview styling is isolated from the published docs theme.
for(const sample of document.querySelectorAll<HTMLElement>('.code-sample')) {
 const button=sample.querySelector<HTMLButtonElement>('.code-copy')!;
 button.addEventListener('click',async()=>{
  try { await navigator.clipboard.writeText(sample.querySelector('code')?.textContent??'');button.textContent='Copied'; }
  catch {button.textContent='Select to copy';}
  setTimeout(()=>{button.textContent='Copy';},1800);
 });
}
const search=document.querySelector<HTMLInputElement>('.guide-search input');
search?.addEventListener('input',()=>{
 const query=search.value.trim().toLowerCase();let count=0;
 for(const group of document.querySelectorAll<HTMLDetailsElement>('.nav-group')) {
  let matches=0;
  for(const link of group.querySelectorAll<HTMLElement>('[data-guide-link]')) {
   link.hidden=!link.textContent?.toLowerCase().includes(query);if(!link.hidden)matches++;
  }
  group.hidden=matches===0;count+=matches;
  group.open=query?matches>0:group.dataset.initialOpen==='true';
 }
 document.querySelector<HTMLElement>('.no-guides')!.hidden=count>0;
});
const links=[...document.querySelectorAll<HTMLAnchorElement>('[data-section-link]')];
const observed=new Map<string,IntersectionObserverEntry>();
const observer=new IntersectionObserver(entries=>{
 for(const entry of entries)observed.set(entry.target.id,entry);
 const visible=[...observed.values()].filter(entry=>entry.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);
 if(!visible.length)return;
 for(const link of links){if(link.hash==='#'+visible[0]!.target.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}
},{rootMargin:'-15% 0px -65% 0px'});
for(const link of links){const section=document.getElementById(link.hash.slice(1));if(section)observer.observe(section);}
