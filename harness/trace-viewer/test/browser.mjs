// Run against the loopback preview with a caller-supplied Playwright installation.
// No dependency or browser download is added to the SDK/runtime package.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const [url,modulePath,output]=process.argv.slice(2);
assert(url&&modulePath&&output,'Usage: browser.mjs LOOPBACK_URL PLAYWRIGHT_MODULE OUTPUT_DIRECTORY');
assert(['127.0.0.1','localhost'].includes(new URL(url).hostname));
const {chromium}=await import(pathToFileURL(resolve(modulePath)).href);
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.getByText('Reported tokens',{exact:true}).waitFor();
 await mkdir(output,{recursive:true});
 await page.screenshot({path:resolve(output,'sdk-trace-usage.png'),fullPage:true});
 await page.getByRole('tab',{name:'Timeline',exact:true}).click();
 await page.getByRole('heading',{name:'Turn timeline'}).waitFor();
 await page.locator('[data-obs-step]').first().click();
 await page.getByText('Record details',{exact:true}).waitFor();
 await page.screenshot({path:resolve(output,'sdk-trace-timeline.png'),fullPage:true});
 // Multiple independently mounted instances, hostile text, subtree/eval panels.
 await page.evaluate(async()=>{
  const {mountTurnTrace}=await import('/src/index.js');
  const evidence=await fetch('/trace.json').then(r=>r.json());
  const adapter={timeline:async()=>evidence.timeline,summary:async()=>evidence.summary,
   children:async()=>({children:[{child_session_id:'child',child_turn_id:'initial',agent_id:'<img src=x onerror=alert(1)>',phase:'ready'}]}),
   subtreeSummary:async()=>({selection:{runs:[{},{}],selection_truncated:false},summary:evidence.summary.summary}),
   evaluations:async()=>({results:[{evaluation:{result:{name:'correctness',status:'completed',score:{value:true},evaluator:{name:'fixture',version:'1',source:'code'},explanation:'<script>alert(1)</script>'}}}],next:null})};
  window.testViews=[];
  for(let i=0;i<2;i++){const root=document.createElement('main');root.id=`test-${i}`;document.body.append(root);window.testViews.push(mountTurnTrace(root,{adapter,sessionId:evidence.timeline.run.session_id,turnId:evidence.timeline.run.turn_id,onChild:child=>{window.selectedChild=child;}}));}
 });
 await page.locator('#test-0').getByRole('heading',{name:'Linked work',exact:true}).waitFor();
 const ids=await page.locator('[id]').evaluateAll(nodes=>nodes.map(n=>n.id));assert.equal(new Set(ids).size,ids.length,'Mounted viewers must have unique DOM IDs');
 assert.equal(await page.locator('#test-0 img, #test-0 script').count(),0,'Content must be escaped');
 await page.locator('#test-0').getByRole('tab',{name:'Usage',exact:true}).focus();await page.keyboard.press('ArrowRight');
 assert.equal(await page.locator('#test-0').getByRole('tab',{name:'Timeline',exact:true}).getAttribute('aria-selected'),'true');
 await page.keyboard.press('Home');
 assert.equal(await page.locator('#test-0').getByRole('tab',{name:'Usage',exact:true}).getAttribute('aria-selected'),'true');
 const child=page.locator('#test-0').getByRole('button',{name:/img src/});await child.focus();await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>window.selectedChild.child_turn_id),'initial');
 await page.locator('#test-1').getByRole('tab',{name:'Timeline',exact:true}).click();
 assert.equal(await page.locator('#test-0').getByRole('tab',{name:'Usage',exact:true}).getAttribute('aria-selected'),'true');
 // The same component's service adapter follows an exact child turn without listing all runs.
 await page.evaluate(async()=>{
  const {mountObservability}=await import('/src/index.js');const data=await fetch('/trace.json').then(r=>r.json());
  const root=document.createElement('main');root.id='management';document.body.append(root);window.managementRequests=[];
  window.managementDispose=mountObservability(root,{initialRun:data.timeline.run,directory:false,openSession:false,children:true,request:async path=>{
   window.managementRequests.push(path);const u=new URL(path,location.origin),parts=u.pathname.split('/');const run={session_id:parts[3],turn_id:parts[5]};
   if(parts[6]==='children')return {children:run.session_id==='child'?[]:[{child_session_id:'child',child_turn_id:'initial',agent_id:'builder',phase:'ready'}]};
   if(parts[6]==='subtree-summary')return {selection:{runs:[{},{}],selection_truncated:false},summary:data.summary.summary};
   if(parts[6]==='timeline')return {...data.timeline,run};if(parts[6]==='summary')return {...data.summary,run};throw Error('Unexpected service request');
  }});
 });
 await page.locator('#management [data-obs-child]').waitFor();await page.locator('#management [data-obs-child]').click();
 await page.waitForFunction(()=>window.managementRequests.some(p=>p.includes('/sessions/child/observability/initial/summary')));
 assert(!(await page.evaluate(()=>window.managementRequests)).some(p=>p.includes('/observability/runs')));
 await page.locator('#management [data-obs="parent"]').click();
 await page.evaluate(()=>window.managementDispose());
 // A large trace stays paged and stops at the documented browser bound.
 await page.evaluate(async()=>{
  const {mountTurnTrace}=await import('/src/index.js');const data=await fetch('/trace.json').then(r=>r.json());
  const root=document.createElement('main');root.id='large';document.body.append(root);
  const base=data.timeline.page.steps[0].step;
  const rows=Array.from({length:1001},(_,i)=>({cursor:i+1,step:{...base,span_id:(i+1).toString(16).padStart(16,'0')}}));
  window.largeRequests=[];
  window.largeView=mountTurnTrace(root,{sessionId:data.timeline.run.session_id,turnId:data.timeline.run.turn_id,adapter:{summary:async()=>data.summary,timeline:async q=>{window.largeRequests.push(q);const start=q.after??0;return {...data.timeline,page:{steps:rows.slice(start,start+100),through:1001,next:start+100<1001?start+100:null}};}}});
 });
 await page.locator('#large').getByText('Reported tokens',{exact:true}).waitFor();
 await page.locator('#large').getByRole('tab',{name:'Timeline',exact:true}).click();
 for(let i=0;i<9;i++) {await page.locator('#large').getByRole('button',{name:'Load more records',exact:true}).click();await page.waitForFunction(n=>document.querySelectorAll('#large [data-obs-step]').length===n,(i+2)*100);}
 assert.equal(await page.locator('#large [data-obs-step]').count(),1000);
 assert.equal(await page.locator('#large').getByRole('button',{name:'Load more records',exact:true}).count(),0);
 assert.equal(await page.evaluate(()=>window.largeRequests.length),10);
 await page.evaluate(()=>window.largeView.dispose());
 await page.evaluate(()=>window.testViews.forEach(v=>v.dispose()));
 assert.equal(await page.locator('#test-0').textContent(),'');assert.deepEqual(errors,[]);
 console.log('Viewer passed: usage, timeline/detail, unique IDs, independent instances, child keyboard navigation, escaped evaluations, disposal.');
}finally{await browser.close();}
