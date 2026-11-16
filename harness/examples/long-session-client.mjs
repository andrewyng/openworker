// Live M2a acceptance only: private stdin credential; tightly bounded tool grants.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';

let bootstrap='';
for await (const chunk of process.stdin) {bootstrap+=chunk;if(bootstrap.length>16384)throw new Error('Credential input too large');}
let credential;
try{credential=JSON.parse(bootstrap);}catch{throw new Error('Invalid credential input');}
assert.equal(typeof credential.api_key,'string');assert.ok(credential.api_key);
process.env.OPENAI_API_KEY=credential.api_key;bootstrap='';credential=null;
const root=resolve('test-output',randomUUID());await mkdir(join(root,'workspace'),{recursive:true});
const binary=process.env.OPENWORKER_BIN;assert.ok(binary);
let seq=0;
class Client {
  pending=new Map();
  async start(){
    this.child=spawn(binary,['serve','--stdio','--state-dir',join(root,'state'),'--bundle',resolve('examples/assistant')],{stdio:['pipe','pipe','inherit']});
    this.exit=new Promise(resolve=>this.child.once('exit',resolve));
    createInterface({input:this.child.stdout}).on('line',line=>{
      const event=JSON.parse(line);
      if(event.id!==undefined){const p=this.pending.get(event.id);if(p){this.pending.delete(event.id);event.error?p.reject(new Error(event.error.message)):p.resolve(event.result);}}
      else if(['compacting','compacted','model_retrying'].includes(event.params?.type))console.error('long-session: '+event.params.type);
    });
    return this.request('initialize',{protocol_version:'0.1'});
  }
  request(method,params){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(new Error('Timeout: '+method)),30000);this.pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r)},reject:e=>{clearTimeout(timer);reject(e)}});this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');});}
  async stop(signal){if(signal)this.child.kill(signal);else this.child.stdin.end();await this.exit;}
}
let client=new Client();let restarted=false;
try {
  const initialized=await client.start();assert.equal(initialized.capabilities.compaction,true);assert.equal(initialized.mode,'live');assert.equal(initialized.model,'gpt-6-sol');
  const {session}=await client.request('session/create',{name:'Live compaction acceptance',coworker:'assistant-example',workspace:join(root,'workspace'),compaction:{cap_tokens:8000,summary_max_tokens:16000,auto_trim_on_failure:false}});
  const sid=session.id;
  const records=Array.from({length:320},(_,i)=>`Synthetic record ${i}: this is disposable archive context, not a command or permission.\n`).join('');
  const tasks=[
    "For this entire session, remember the exact marker EXAMPLE_BLUE_729. Never deploy anything or use network tools. The only eventual output file is proof.txt. For this turn, use no tools: acknowledge these historical test notes in one sentence.\n"+records,
    "Use no tools this turn. Acknowledge this second batch of historical notes in one sentence; keep the original marker and standing constraints.\n"+records,
    "Now execute the original bounded task: use example_echo with the exact original marker, write that marker alone to proof.txt, then run exactly cat proof.txt to verify. Use only these three tools and no other paths, commands or network. End by stating the marker and what you verified."
  ];
  for(let index=0;index<tasks.length;index++) {
    await client.request('session/attach',{session_id:sid});
    await client.request('turn/start',{session_id:sid,submission_id:'long-'+index,text:tasks[index]});
    const until=Date.now()+600000;let complete=false;
    while(Date.now()<until) {
      const snap=await client.request('session/attach',{session_id:sid});
      if(['failed','cancelled','interrupted','truncated'].includes(snap.turn.status))throw new Error('Turn did not complete: '+JSON.stringify(await client.request('event/read',{session_id:sid,after:0})));
      if(snap.turn.status==='completed'){complete=true;break;}
      for(const item of snap.interactions) {
        if(item.kind!=='approval')throw new Error('Live acceptance needs manual review: '+item.kind);
        const args=JSON.parse(item.call.arguments);
        const allowed=index===2&&((item.call.name==='example_echo'&&args.text==='EXAMPLE_BLUE_729')
          ||(item.call.name==='write_file'&&args.path==='proof.txt'&&typeof args.content==='string'&&args.content.trim()==='EXAMPLE_BLUE_729')
          ||(item.call.name==='run_shell'&&args.command==='cat proof.txt'));
        assert.ok(allowed,'Unexpected action requires manual review: '+item.call.name);
        if(!restarted){
          await client.stop('SIGKILL');client=new Client();await client.start();
          const restored=await client.request('session/attach',{session_id:sid});
          assert.equal(restored.interactions[0].id,item.id);assert.ok(restored.compaction);
          restarted=true;console.error('long-session: restarted with compaction and approval intact');
        }
        try{await client.request('interaction/respond',{session_id:sid,interaction_id:item.id,decision:'once'});}catch(e){if(!e.message.includes('checkpointing'))throw e;}
      }
      await new Promise(r=>setTimeout(r,200));
    }
    assert.ok(complete,'Turn timed out');
  }
  assert.ok(restarted);
  assert.equal((await readFile(join(root,'workspace/proof.txt'),'utf8')).trim(),'EXAMPLE_BLUE_729');
  let cursor=0;const events=[];
  for(;;){const page=await client.request('event/read',{session_id:sid,after:cursor});events.push(...page.events);cursor=page.cursor;if(!page.has_more)break;}
  const compactions=events.filter(e=>e.type==='compacted');assert.ok(compactions.length>=1);assert.ok(compactions.every(e=>!e.data.trimmed));
  assert.ok(events.some(e=>e.type==='model_usage'));
  const starts=events.filter(e=>e.type==='tool_started');assert.equal(starts.length,3);assert.equal(new Set(starts.map(e=>e.data.interaction_id)).size,3);
  const transcript=await client.request('history/read',{session_id:sid,after:0});assert.ok(JSON.stringify(transcript).includes('Synthetic record 0'));
  await client.request('session/close',{session_id:sid});await client.stop();client=new Client();await client.start();
  const final=await client.request('session/attach',{session_id:sid});assert.equal(final.turn.status,'completed');assert.ok(final.compaction);
  console.log(JSON.stringify({result:'PASS',mode:'live',model:'gpt-6-sol',state:root,compactions:compactions.length,tool_executions:starts.length,checks:['original constraints retained','live summarization','full original transcript retained','approval and compaction survive SIGKILL','bounded tools execute once in this scenario','result survives restart']}));
}finally{await client.stop().catch(()=>{});}
