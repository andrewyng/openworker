// Developer-only bridge: explicitly authorized existing OpenWorker OpenAI key.
// Nothing from the secret store is logged, copied into artifacts, or put in argv.
import {readFile} from 'node:fs/promises';
import {homedir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawn} from 'node:child_process';

const store=join(homedir(),'.config/coworker');
const profile=JSON.parse(await readFile(join(store,'secrets.json'),'utf8'))['provider:openai'];
let key=profile?.api_key;
if(typeof key!=='string'||!key)throw new Error('No configured OpenAI key');
const ref=key.match(/^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/);
if(ref){
  const contents=await readFile(join(store,'.env'),'utf8').catch(()=> '');
  const line=contents.split('\n').find(l=>l.split('=')[0].trim()===ref[1]);
  key=process.env[ref[1]]||line?.slice(line.indexOf('=')+1).trim().replace(/^["']|["']$/g,'');
}
if(!key||key.includes('${'))throw new Error('OpenAI credential reference unresolved');
const root=resolve('.');const config=join(homedir(),'.config/openshell');
const active=(await readFile(join(config,'active_gateway'),'utf8')).trim();
if(!/^[\w-]+$/.test(active))throw new Error('Unexpected gateway identity');
const binary=join(root,'target/linux-arm64/release/openworker');
const args=['run','--rm','-i','--name','owr-example-live-acceptance','--user',`${process.getuid()}:${process.getgid()}`,'--group-add','0',
  '--mount',`type=bind,source=${root},target=${root}`,
  '--mount',`type=bind,source=${config}/active_gateway,target=/gateway-config/openshell/active_gateway,readonly`,
  '--mount',`type=bind,source=${config}/gateways/${active},target=/gateway-config/openshell/gateways/${active},readonly`,
  '--mount','type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock',
  '-w',root,'-e','XDG_CONFIG_HOME=/gateway-config','-e','DOCKER_CONFIG=/tmp/docker-client',
  '-e',`OPENWORKER_BIN=${binary}`,'-e',`OPENWORKER_RUNNER=${binary}`,
  'owr-example-linux-client:20260929','node','tests/linux-client/entry.mjs','--credential-stdin','--live'];
const child=spawn('docker',args,{stdio:['pipe','inherit','inherit']});
child.stdin.end(JSON.stringify({api_key:key}));key='';
process.exitCode=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',code=>resolve(code??1));});
