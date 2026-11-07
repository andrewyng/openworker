// Linux qualification: remove debug symbols before tests that hash executable identity.
// Debug info otherwise dominates fixture hashing; executable code is unchanged.
import { spawnSync } from 'node:child_process';
const run = (program,args,options={}) => {
  const r=spawnSync(program,args,{encoding:'utf8',...options});
  if(r.error) throw r.error;
  if(r.status!==0) { if(r.stdout)process.stdout.write(r.stdout);if(r.stderr)process.stderr.write(r.stderr);process.exit(r.status??1); }
  return r;
};
const suite = process.env.OW_SDK_TEST_SUITE ?? 'dynamic_agents';
if(!['dynamic_agents','desktop_api','agents','agents_regressions','storage_teams','conformance'].includes(suite)) throw Error('Unsupported SDK test suite');
const built=run('cargo',['test','--locked','-p','openworker-sdk','--features','test-support','--test',suite,'--no-run','--message-format=json'],{maxBuffer:16*1024*1024});
process.stderr.write(built.stderr);
const records=built.stdout.trim().split('\n').map(line=>JSON.parse(line));
const artifacts=records.filter(r=>r.reason==='compiler-artifact'&&r.executable);
const tests=artifacts.filter(r=>r.target.name===suite);
if(tests.length!==1) throw Error('Missing dynamic agent test executable');
for(const artifact of artifacts.filter(r=>['sdk-test-host',suite].includes(r.target.name))) run('strip',['--strip-debug',artifact.executable]);
run(tests[0].executable,['--nocapture',...process.argv.slice(2)],{stdio:'inherit'});
