// Exact-owned local fixture only. No cloud resources, provider keys or model calls.
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile, unlink } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
const exec=promisify(execFile);
const repo=resolve(fileURLToPath(new URL("../..",import.meta.url)));
const id=randomUUID().replaceAll("-","").slice(0,12);
const root=resolve(process.argv[2]??join(repo,`.ow-service-observability-${id}`));
await mkdir(root,{mode:0o700}); // Refuse reuse of another qualification's state.
assert.equal((await exec("git",["check-ignore",join(root,"private")],{cwd:repo})).stdout.trim(),join(root,"private"),"Qualification state must be gitignored");
const image="postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685";
const name=`ow-observability-pg-${id}`,label="openworker.observability-qualification";
const password=randomBytes(24).toString("hex");
const secret=join(root,"postgres-password");await writeFile(secret,password,{mode:0o600,flag:"wx"});
const docker=async(...args)=>(await exec("docker",args,{timeout:60000,maxBuffer:1048576})).stdout.trim();
const results=[];let intent=false,removed=false,writeProbe=null,queryProbe=null;
// Optional pinned image built by Dockerfile.observability. Never pull a remote
// image or pass model/cloud credentials into this native-Linux recovery fixture.
const linuxImage=process.env.OW_OBSERVABILITY_LINUX_IMAGE;
if(linuxImage)assert.match(linuxImage,/^sha256:[a-f0-9]{64}$/);
async function linuxRecovery(pg){
  const envFile=join(root,"linux-test-env");
  const base=`postgresql://openworker:${password}@127.0.0.1:5432`;
  await writeFile(envFile,`OW_PG_TEST_URL=${base}/execution\nOW_EXECUTION_TEST_URL=${base}/execution\nOW_OBSERVABILITY_TEST_URL=${base}/observability\n`,{mode:0o600,flag:"wx"});
  try{
    for(const [testName,entrypoint,args] of [
      ["linux-worker-recovery","/out/worker-recovery",["--ignored","--test-threads=1","--skip","mixed_versions_keep_native_identity_pinned_and_old_pool_can_resume","--skip","bounded_load_matrix"]],
      ["linux-authorized-http","/out/recording-queries",["--ignored","--test-threads=1","--nocapture"]],
    ]){
      let owned;
      try{
        owned=await docker("create","--pull=never","--name",`ow-${testName}-${id}`,"--label",`${label}=${id}`,"--network",`container:${pg}`,"--memory=2g","--env-file",envFile,"--entrypoint",entrypoint,linuxImage,...args);
        const output=await exec("docker",["start","-a",owned],{timeout:600000,maxBuffer:4194304});
        await writeFile(join(root,`${testName}.log`),output.stdout+output.stderr,{mode:0o600,flag:"wx"});
        const inspected=JSON.parse(await docker("inspect",owned))[0];assert.equal(inspected.State.ExitCode,0,`${testName} failed; inspect private fixture log`);
        const probe=(output.stdout+output.stderr).match(/OBSERVABILITY_QUERY_PROBE (\{[^\n]+\})/);if(probe)queryProbe={...JSON.parse(probe[1]),platform:"linux"};
        results.push(testName);console.log(`PASS ${testName}`);
      }finally{if(owned){const inspected=JSON.parse(await docker("inspect",owned))[0];assert.equal(inspected.Config.Labels[label],id);await docker("rm","-f",owned);}}
    }
    await entrypointSmoke(pg,base);
  }finally{
    await unlink(envFile);
  }
}
async function entrypointSmoke(pg,base){
  const executionFile=join(root,"linux-execution-url"),recordingFile=join(root,"linux-recording-url"),configFile=join(root,"linux-install-config.json");
  const schema=`ow_install_${id}`,recordingSchema=`ow_record_install_${id}`;
  await writeFile(executionFile,`${base}/execution`,{mode:0o600,flag:"wx"});
  await writeFile(recordingFile,`${base}/observability`,{mode:0o600,flag:"wx"});
  await writeFile(configFile,JSON.stringify({database_env:"OW_PG_URL",schema,database_loopback_plaintext:true,state_root:"/tmp/state",bundle:"unused",runner:"unused",model:"fixture",scripted:true,api_token_file:"unused",actor:"install-test",openshell:{identity:"unused",endpoint:"https://127.0.0.1:1",certificates:"unused",workspace:"unused",image:"unused"},observability:{schema:recordingSchema,database:{database_env:"OW_OBSERVABILITY_URL",database_loopback_plaintext:true}}}),{mode:0o600,flag:"wx"});
  let owned;
  try{
    owned=await docker("create","--pull=never","--name",`ow-recording-install-${id}`,"--label",`${label}=${id}`,"--network",`container:${pg}`,"--memory=512m",
      "--mount",`type=bind,source=${executionFile},target=/run/service/database-url,readonly`,"--mount",`type=bind,source=${recordingFile},target=/run/observability/database-url,readonly`,"--mount",`type=bind,source=${configFile},target=/run/service/config.json,readonly`,"--entrypoint","/bin/sh",linuxImage,"/opt/openworker/entrypoint.sh","migrate");
    await docker("start","-a",owned);const inspected=JSON.parse(await docker("inspect",owned))[0];assert.equal(inspected.State.ExitCode,0,"Packaged migration failed");
    const execution=await docker("exec",pg,"psql","-U","openworker","-d","execution","-Atc",`SELECT to_regclass('${schema}.version') IS NOT NULL AND to_regclass('${recordingSchema}.observation_version') IS NULL`);
    const recording=await docker("exec",pg,"psql","-U","openworker","-d","observability","-Atc",`SELECT to_regclass('${recordingSchema}.observation_version') IS NOT NULL AND to_regclass('${schema}.version') IS NULL`);
    assert.equal(execution,"t");assert.equal(recording,"t");results.push("linux-packaged-two-database-install");console.log("PASS linux-packaged-two-database-install");
  }finally{
    if(owned){const inspected=JSON.parse(await docker("inspect",owned))[0];assert.equal(inspected.Config.Labels[label],id);await docker("rm","-f",owned);}
    await Promise.all([executionFile,recordingFile,configFile].map(p=>unlink(p)));
  }
}
async function test(name,args,variables={}){
  const env={...process.env,...variables};
  // Keep application/model/AWS credentials out of the fixture process.
  for(const k of Object.keys(env))if(k.startsWith("AWS_")||/^(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY$/.test(k))delete env[k];
  const child=spawn("cargo",["test","--offline","--locked","--manifest-path",join(repo,"harness/Cargo.toml"),...args],{cwd:repo,env,stdio:["ignore","pipe","pipe"]});
  let output="";child.stdout.on("data",b=>output+=b);child.stderr.on("data",b=>output+=b);
  const timer=setTimeout(()=>child.kill("SIGTERM"),300000);
  const status=await new Promise((resolve,reject)=>{child.on("error",reject);child.on("close",resolve);});clearTimeout(timer);
  await writeFile(join(root,`${name}.log`),output,{mode:0o600,flag:"wx"});
  assert.equal(status,0,`${name} failed; inspect its private qualification log`);
  results.push(name);console.log(`PASS ${name}`);
  const probe=output.match(/OBSERVABILITY_WRITE_PROBE (\{[^\n]+\})/);if(probe)writeProbe=JSON.parse(probe[1]);
  const query=output.match(/OBSERVABILITY_QUERY_PROBE (\{[^\n]+\})/);if(query)queryProbe={...JSON.parse(query[1]),platform:"host"};
}
try{
  await docker("image","inspect",image);
  await writeFile(join(root,"create-intent.json"),JSON.stringify({name,image,label,id}),{mode:0o600,flag:"wx"});intent=true;
  const container=await docker("run","--pull=never","-d","--name",name,"--label",`${label}=${id}`,"--memory=512m","--tmpfs","/var/lib/postgresql/data:rw,size=512m","-p","127.0.0.1::5432","-e","POSTGRES_USER=openworker","-e","POSTGRES_DB=execution","-e","POSTGRES_PASSWORD_FILE=/run/secrets/password","--mount",`type=bind,source=${secret},target=/run/secrets/password,readonly`,image);
  const inspected=JSON.parse(await docker("inspect",name))[0];assert.equal(inspected.Id,container);assert.equal(inspected.Config.Labels[label],id);
  const ports=inspected.NetworkSettings.Ports["5432/tcp"];assert.equal(ports.length,1);assert.equal(ports[0].HostIp,"127.0.0.1");
  const until=Date.now()+60000;let ready=false;while(Date.now()<until){try{await docker("exec",name,"pg_isready","-U","openworker","-d","execution");ready=true;break;}catch{}await new Promise(r=>setTimeout(r,300));}assert(ready,"Owned PostgreSQL fixture not ready");
  await docker("exec",name,"createdb","-U","openworker","observability");
  const base=`postgresql://openworker:${password}@127.0.0.1:${ports[0].HostPort}`;
  const vars={OW_EXECUTION_TEST_URL:`${base}/execution`,OW_OBSERVABILITY_TEST_URL:`${base}/observability`};
  await test("postgres-storage-contract",["-p","openworker-observability","--all-features","--test","storage","--","--ignored","--nocapture"],vars);
  await test("service-two-database-outage",["-p","openworker-server-reference","--test","recording","--","--ignored"],vars);
  await test("postgres-recording-queries",["-p","openworker-observability","--all-features","--test","queries","--","--ignored"],vars);
  await test("authorized-recording-http",["-p","openworker-server-reference","--test","recording_queries","--","--ignored","--nocapture"],vars);
  if(linuxImage)await linuxRecovery(container);
}finally{
  if(intent){
    const i=JSON.parse(await docker("inspect",name))[0];assert.equal(i.Config.Labels[label],id,"Refusing to remove unowned container");
    await docker("rm","-f",i.Id);removed=true;
    await unlink(secret); // Generated fixture password only; original credentials untouched.
  }
  const report={version:1,at:new Date().toISOString(),scope:"local Docker PostgreSQL; no AWS or paid models",postgresImage:image,linuxImage:linuxImage??null,databases:2,tests:results,writeProbe,queryProbe,passed:results.length===(linuxImage?7:4)&&removed,ownedContainerRemoved:removed,generatedPasswordRemoved:removed,unrelatedContainersModified:false};
  await writeFile(join(root,"qualification.json"),JSON.stringify(report,null,2)+"\n",{mode:0o600,flag:"wx"});
  console.log(JSON.stringify(report));
}
