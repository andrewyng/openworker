import { previewHtml } from "./preview.js";
const TEST_STATE = `<script>(()=>{
const slots=new Map(),scores=new Map();
const key=k=>{if(typeof k!=='string'||!(/^[a-zA-Z0-9_-]{1,40}$/).test(k))throw Error('Invalid slot');return k};
const ver=v=>{if(!Number.isInteger(v)||v<1||v>10000)throw Error('Invalid version');return v};
const clone=v=>JSON.parse(JSON.stringify(v));
Object.defineProperty(window,'arcadeSave',{value:Object.freeze({
async load(k,v){key(k);ver(v);const r=slots.get(k);if(r&&r.version!==v)throw Error('Save version mismatch');return r?clone(r):null},
async save(k,v,value){key(k);ver(v);const r={version:v,value:clone(value),updatedAt:Date.now()};if(JSON.stringify(r).length>262144||(!slots.has(k)&&slots.size>=16))throw Error('Test save limit');const next=new Map(slots);next.set(k,r);if(JSON.stringify([...next]).length>1048576)throw Error('Test save limit');slots.set(k,r);return clone(r)},
async remove(k){slots.delete(key(k))},
async best(k){return clone(scores.get(key(k))??null)},
async submitScore(k,score,order){key(k);if(!Number.isFinite(score)||score<0||score>1e12||!['lower','higher'].includes(order))throw Error('Invalid score');const old=scores.get(k);if(old&&old.order!==order)throw Error('Score ordering changed');if(!old&&scores.size>=64)throw Error('Test score limit');if(!old||(order==='lower'?score<old.score:score>old.score))scores.set(k,{score,order,updatedAt:Date.now()});return clone(scores.get(k))}
}),writable:false,configurable:false});
addEventListener('error',e=>console.error('Runtime error: '+e.message));
addEventListener('unhandledrejection',e=>console.error('Unhandled rejection: '+String(e.reason).slice(0,500)));
})();</script>`;
export function temporaryPreviewHtml(html: string): string {
  return previewHtml(html, false).replace(/(<script>)/, TEST_STATE + "$1");
}
