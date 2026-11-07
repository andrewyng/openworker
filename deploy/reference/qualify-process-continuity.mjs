// Exercise independent MCP attachments against one environment-owned manager.
// No model key, host mount, Docker socket or network access enters the container.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
const image=process.argv[2];assert(image&&/^[A-Za-z0-9./_:@-]+$/.test(image),'Trusted image required');
const container='ow-process-continuity-'+randomUUID();
execFileSync('docker',['run','-d','--rm','--name',container,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m','--tmpfs','/sandbox:rw,size=64m','--tmpfs','/tmp:rw,size=64m',image],{stdio:'pipe'});
const clients=[];
function connect(workspace='/sandbox'){
 const child=spawn('docker',['exec','-i',container,'/opt/openworker/openworker','mcp-coding','--workspace',workspace,'--environment-processes'],{stdio:['pipe','pipe','pipe']});
 let closed=false;let id=0;const pending=new Map();let stderr='';child.stderr.on('data',b=>stderr=(stderr+b).slice(-2000));
 const lines=createInterface({input:child.stdout});lines.on('line',s=>{const r=JSON.parse(s),p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(Error('RPC error')):p.resolve(r.result)}});
 child.on('exit',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('MCP closed: '+stderr))}});
 const rpc=(method,params={})=>new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>reject(Error('MCP timeout')),20000);pending.set(n,{resolve,reject,timer});child.stdin.write(JSON.stringify({jsonrpc:'2.0',id:n,method,params})+'\n')});
 const call=async(name,args)=>{const r=await rpc('tools/call',{name,arguments:args});const v=JSON.parse(r.content[0].text);return {...v,mcpError:r.isError===true}};
 const close=async()=>{if(closed||child.exitCode!==null)return;closed=true;child.stdin.end();await Promise.race([new Promise(r=>child.once('exit',r)),new Promise((_,reject)=>setTimeout(()=>reject(Error('Attachment did not exit')),5000).unref())]);lines.close()};
 const c={rpc,call,close};clients.push(c);return c;
}
async function init(c){await c.rpc('initialize',{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'continuity',version:'1'}});}
try{
 const a=connect(),racing=connect();await Promise.all([init(a),init(racing)]);
 const raced=await Promise.all([a.call('process_start',{command:'node -e "setInterval(()=>{},1000)"',timeout_seconds:30}),racing.call('process_start',{command:'node -e "setInterval(()=>{},1000)"',timeout_seconds:30})]);
 assert(raced.every(p=>p.process_id&&!p.mcpError));assert.equal(raced[0].process_id.split('_')[1],raced[1].process_id.split('_')[1]);
 await Promise.all([a.call('process_stop',{process_id:raced[0].process_id}),racing.call('process_stop',{process_id:raced[1].process_id})]);await racing.close();
 const p=await a.call('process_start',{command:`node -e 'const h=require("http");h.createServer((q,s)=>s.end("alive")).listen(4000,"127.0.0.1");setInterval(()=>console.log("tick"),100)'`,timeout_seconds:120});assert(p.process_id&&!p.mcpError);
 await a.close();
 const b=connect();await init(b);assert.equal((await b.call('process_status',{process_id:p.process_id})).status,'running');
 const health=await b.call('run_shell',{command:'curl --max-time 2 -sS http://127.0.0.1:4000',yield_ms:10000});assert.equal(health.stdout,'alive');
 await new Promise(r=>setTimeout(r,250));assert((await b.call('process_logs',{process_id:p.process_id})).stdout.includes('tick'));
 // Deliberately drop the launch response. The caller already knows the opaque
 // launch ID, so it checks status; it never repeats the start request.
 const lostScript=`const net=require('net'),crypto=require('crypto');let buf='';const s=net.createConnection('/sandbox/.openworker-processes/manager.sock');s.on('data',b=>{buf+=b;const i=buf.indexOf('\\n');if(i<0)return;const hello=JSON.parse(buf.slice(0,i));const id='process_'+hello.generation+'_'+crypto.randomUUID().replaceAll('-','');s.removeAllListeners('data');s.write(JSON.stringify({name:'process_start',args:{command:'node -e "setInterval(()=>{},1000)"',timeout_seconds:120},launch_id:id})+'\\n',()=>{console.log(id);s.destroy()})});`;
 const lost=execFileSync('docker',['exec',container,'node','-e',lostScript],{encoding:'utf8',timeout:5000}).trim();
 const state=await b.call('process_status',{process_id:lost});assert.equal(state.status,'running');
 await b.call('process_stop',{process_id:lost});
 const short=await b.call('process_start',{command:'node -e "setInterval(()=>{},1000)"',timeout_seconds:1});
 await new Promise(r=>setTimeout(r,1200));assert.equal((await b.call('process_status',{process_id:short.process_id})).status,'timed_out');
 await b.call('run_shell',{command:'mkdir other',yield_ms:10000});const c=connect('/sandbox/other');await init(c);
 const other=await c.call('process_start',{command:'node -e "setInterval(()=>{},1000)"',timeout_seconds:30});
 const stale=await c.call('process_stop',{process_id:p.process_id});assert(stale.mcpError&&stale.executed===false&&stale.code==='stale_process_handle');
 assert.equal((await b.call('process_status',{process_id:p.process_id})).status,'running');
 await c.call('process_stop',{process_id:other.process_id});await c.close();
 await b.call('process_stop',{process_id:p.process_id});assert.equal((await b.call('process_status',{process_id:p.process_id})).status,'stopped');await b.close();
 console.log(JSON.stringify({passed:true,concurrentManagerStartup:true,independentMcpAttachments:true,httpAndLogsAfterDisconnect:true,lostLaunchReplyCheckedWithoutReplay:true,timeoutAndStop:true,crossWorkspaceHandleRejected:true,network:'none',hostCredentialsPassed:false}));
}finally{
 for(const c of clients){try{await c.close()}catch{}}
 execFileSync('docker',['rm','-f',container],{stdio:'pipe'});
}
