/** Build a deterministic folder hierarchy from the explicit source manifest. */
export function sourceTree(files) {
 const root={name:'',path:'',kind:'folder',children:[]};const seen=new Set();
 for(const file of files){
  if(typeof file.path!=='string'||typeof file.content!=='string')throw Error('Invalid source file');
  const parts=file.path.split('/');
  if(parts.some(part=>!part||part==='.'||part==='..'||part.includes('\\'))||seen.has(file.path))throw Error('Invalid or duplicate source path');
  seen.add(file.path);let parent=root;
  for(let i=0;i<parts.length;i++){
   const name=parts[i],path=parts.slice(0,i+1).join('/'),kind=i===parts.length-1?'file':'folder';
   let node=parent.children.find(child=>child.name===name);
   if(node&&node.kind!==kind)throw Error('Conflicting source paths');
   if(!node){node=kind==='folder'?{name,path,kind,children:[]}:{name,path,kind,file};parent.children.push(node);}
   parent=node;
  }
 }
 function sort(node){node.children?.sort((a,b)=>(a.kind===b.kind?0:a.kind==='folder'?-1:1)||a.name.localeCompare(b.name));node.children?.forEach(sort);}
 sort(root);return root;
}
export function visibleSourceRows(root,expanded,query='') {
 const filter=query.trim().toLowerCase(),rows=[];
 function matches(node){return node.path.toLowerCase().includes(filter)||node.children?.some(matches);}
 function visit(parent,depth){
  const children=parent.children.filter(node=>!filter||matches(node));
  children.forEach((node,index)=>{
   const open=node.kind==='folder'&&(!!filter||expanded.has(node.path));
   rows.push({node,depth,open,position:index+1,size:children.length});
   if(open)visit(node,depth+1);
  });
 }
 visit(root,1);return rows;
}
export function sourceLanguage(path) {
 const ext=path.split('.').pop()?.toLowerCase();
 if(/(^|\/)Dockerfile$/.test(path))return {id:'docker',label:'Dockerfile',icon:'◈'};
 if(ext==='ts'||ext==='tsx')return {id:ext,label:'TypeScript',icon:'TS'};
 if(['js','mjs','cjs','jsx'].includes(ext))return {id:ext==='jsx'?'jsx':'js',label:'JavaScript',icon:'JS'};
 if(ext==='rs')return {id:'rust',label:'Rust',icon:'RS'};
 if(ext==='json')return {id:'json',label:'JSON',icon:'{}'};
 if(ext==='html')return {id:'html',label:'HTML',icon:'<>'};
 if(ext==='css')return {id:'css',label:'CSS',icon:'#'};
 if(ext==='md')return {id:'markdown',label:'Markdown',icon:'M↓'};
 if(ext==='toml')return {id:'toml',label:'TOML',icon:'≡'};
 return {id:'text',label:'Plain text',icon:'·'};
}
