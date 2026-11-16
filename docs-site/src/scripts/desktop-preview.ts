import './guide';
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
