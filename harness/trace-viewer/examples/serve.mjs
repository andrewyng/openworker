// Loopback-only viewer for a selected synthetic qualification result.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
const [evidence,port='59602']=process.argv.slice(2);
if(!evidence)throw Error('Usage: node examples/serve.mjs EVIDENCE_JSON [PORT]');
const files=new Map([['/',new URL('index.html',import.meta.url)],...['index.js','observability.js','observability-data.js','style.css'].map(f=>[`/src/${f}`,new URL(`../src/${f}`,import.meta.url)])]);
const types={js:'text/javascript',html:'text/html',css:'text/css',json:'application/json'};
createServer(async(req,res)=>{
 try {const path=new URL(req.url,'http://local').pathname;const file=path==='/trace.json'?evidence:files.get(path);if(!file){res.writeHead(404);res.end();return;}const body=await readFile(file);res.writeHead(200,{'content-type':path==='/'?'text/html':types[path.split('.').pop()]??'text/plain','cache-control':'no-store'});res.end(body);}catch{res.writeHead(500);res.end('Unavailable');}
}).listen(Number(port),'127.0.0.1',()=>console.log(`Viewer: http://127.0.0.1:${port}`));
