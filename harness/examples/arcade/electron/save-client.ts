// Injected before game code; only a narrow MessagePort API, never Electron IPC.
export const SAVE_CLIENT = `<script>(()=>{
 let port,sequence=0;const pending=new Map();let connected;const ready=new Promise(r=>connected=r);
 addEventListener('message',e=>{if(e.source!==parent||e.data?.type!=='arcade.save.connect'||!e.ports[0])return;
   if(port)port.close();port=e.ports[0];port.onmessage=e=>{const r=e.data,p=pending.get(r?.id);if(!p)return;pending.delete(r.id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(r.error||'Save failed.'));};port.start();connected();
 });
 async function call(request){let wait;try{await Promise.race([ready,new Promise((_,reject)=>wait=setTimeout(()=>reject(new Error('Save service unavailable.')),5000))]);}finally{clearTimeout(wait);}if(pending.size>=4)throw new Error('Too many save requests.');return new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(new Error('Save timed out.'));},8000);pending.set(id,{resolve,reject,timer});try{port.postMessage({id,request});}catch(e){pending.delete(id);clearTimeout(timer);reject(e);}});}
 Object.defineProperty(window,'arcadeSave',{value:Object.freeze({
 load:(slot,version)=>call({op:'load',slot,version}),save:(slot,version,value)=>call({op:'save',slot,version,value}),remove:slot=>call({op:'remove',slot}),best:board=>call({op:'best',board}),submitScore:(board,score,order='lower')=>call({op:'score',board,score,order})
 }),writable:false,configurable:false});
})();</script>`;
