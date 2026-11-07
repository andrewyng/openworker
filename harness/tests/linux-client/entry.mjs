// Forward the existing developer gateway without changing its configuration or
// TLS identity. This bridge is a test topology, not part of the runtime release.
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
// Optional test-only credential bootstrap. It enters through stdin, never a
// Docker environment definition, command-line argument, mount, or on-disk file.
const args=process.argv.slice(2);
if(args[0]==='--credential-stdin'){
  args.shift();let secret='';
  for await(const chunk of process.stdin){secret+=chunk;if(secret.length>16384)throw new Error('Credential bootstrap exceeds limit');}
  const credential=JSON.parse(secret);
  if(typeof credential.api_key!=='string'||!credential.api_key)throw new Error('Missing bootstrap credential');
  process.env.OPENAI_API_KEY=credential.api_key;
  secret='';
}
const home=process.env.XDG_CONFIG_HOME||join(process.env.HOME,'.config');
const active=(await readFile(join(home,'openshell/active_gateway'),'utf8')).trim();
const metadata=JSON.parse(await readFile(join(home,'openshell/gateways',active,'metadata.json'),'utf8'));
const endpoint=new URL(metadata.gateway_endpoint);
if(!['localhost','127.0.0.1'].includes(endpoint.hostname))throw new Error('This test bridge requires a local developer gateway');
const port=endpoint.port;
const proxy=spawn('socat',[`TCP-LISTEN:${port},bind=127.0.0.1,reuseaddr,fork`,`TCP:host.docker.internal:${port}`],{stdio:'inherit'});
await new Promise(resolve=>setTimeout(resolve,500));
const native=args[0]==='--native';
const child=native?spawn(process.env.OPENWORKER_BIN,args.slice(1),{stdio:'inherit',env:process.env}):spawn('node',['examples/stdio-client.mjs',...args],{stdio:'inherit',env:process.env});
const code=await new Promise(resolve=>child.on('exit',resolve));
proxy.kill();process.exitCode=code||0;
