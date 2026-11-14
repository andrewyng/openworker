// Opt-in real nginx test: no cloud access, credentials or model calls.
// OWS_PROXY_TEST=1 node --test deploy/service/proxy.test.mjs
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import https from 'node:https';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const exec=promisify(execFile);
const run=async(program,args)=>String((await exec(program,args,{maxBuffer:8*1024*1024})).stdout);
test('real TLS proxy accepts SDK attachment chunks and preserves scoped limits/authentication',{skip:process.env.OWS_PROXY_TEST!=='1',timeout:60000},async()=>{
  const root=await mkdtemp(join(tmpdir(),'ows-proxy-'));
  const name='ows-proxy-'+randomUUID().slice(0,8);
  let forwarded=0;
  const upstream=createServer(async(req,res)=>{
    forwarded++;
    const chunks=[];for await(const c of req)chunks.push(c);
    if(req.headers.authorization!=='Bearer fixture'){res.writeHead(401).end();return;}
    const body=Buffer.concat(chunks);
    res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({bytes:body.length,sha256:createHash('sha256').update(body).digest('hex')}));
  });
  await new Promise(r=>upstream.listen(0,'0.0.0.0',r));
  try{
    const values=join(root,'values.json');
    await writeFile(values,JSON.stringify({image:'fixture@sha256:'+'a'.repeat(64),config:{service:{id:'proxy-test',namespace:'test',agent:'fixture'}}}));
    const rendered=await run('helm',['template','proxy-test',resolve('deploy/service/chart'),'-f',values]);
    const raw=rendered.split('  nginx.conf: |\n')[1].split('\n---')[0].replace(/^    /gm,'');
    const conf=raw.replaceAll('127.0.0.1:8080',`host.docker.internal:${upstream.address().port}`);
    await writeFile(join(root,'nginx.conf'),conf);
    await run('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,digitalSignature,keyCertSign,cRLSign','-addext','extendedKeyUsage=serverAuth','-keyout',join(root,'tls.key'),'-out',join(root,'tls.crt')]);
    await run('openssl',['verify','-x509_strict','-purpose','sslserver','-CAfile',join(root,'tls.crt'),join(root,'tls.crt')]);
    const ca=await readFile(join(root,'tls.crt'));
    const image=(await readFile('deploy/service/chart/values.yaml','utf8')).match(/^proxyImage: (.+)$/m)[1];
    await run('docker',['run','--rm','-d','--name',name,'-p','127.0.0.1::8443','-v',`${root}:/tls:ro`,image,'nginx','-c','/tls/nginx.conf','-g','daemon off;']);
    const port=Number((await run('docker',['port',name,'8443/tcp'])).trim().split(':').at(-1));
    const request=(path,body,auth=true)=>new Promise((resolve,reject)=>{
      const q=https.request({hostname:'127.0.0.1',port,path,method:'POST',ca,agent:false,headers:{expect:'100-continue','content-type':'application/json','content-length':body.length,...auth?{authorization:'Bearer fixture'}:{}}},r=>{let b='';r.on('data',c=>b+=c);r.on('end',()=>resolve({status:r.statusCode,body:b}));});
      q.on('error',reject);q.on('continue',()=>q.end(body));q.flushHeaders();
    });
    for(let i=0;;i++){try{await request('/healthz',Buffer.alloc(0));break;}catch(e){if(i===30)throw e;await new Promise(r=>setTimeout(r,100));}}
    const path='/v1/sessions/session_123/attachments/file_123';
    const chunk=Buffer.from(JSON.stringify({offset:0,data:Buffer.alloc(256*1024,97).toString('base64')}));
    assert(chunk.length>160*1024&&chunk.length<1024*1024);
    const ok=await request(path,chunk);assert.equal(ok.status,200);
    assert.equal(JSON.parse(ok.body).sha256,createHash('sha256').update(chunk).digest('hex'));
    assert.equal((await request(path,chunk,false)).status,401);
    const count=forwarded;
    for(const p of ['/v1/sessions/session_123/turns','/v1/sessions/session_123/attachments','/v1/sessions/session_123/attachments/file_123/extra'])assert.equal((await request(p,chunk)).status,413);
    assert.equal((await request(path,Buffer.alloc(1024*1024+1))).status,413);
    assert.equal(forwarded,count,'oversized requests must not reach the application');
  }finally{
    await run('docker',['rm','-f',name]).catch(()=>{});
    await new Promise(r=>upstream.close(r));await rm(root,{recursive:true,force:true});
  }
});
