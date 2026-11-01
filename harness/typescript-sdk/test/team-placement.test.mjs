import test from "node:test";
import assert from "node:assert/strict";
import {sandboxWire,serviceDelegationWire} from "../dist/team-placement.js";
import {runtimeProfileGrants} from "../dist/service.js";
test("Git runtime profile helper preserves exact named grant and never invents authority",()=>{
  const profile={id:"git@1",provider:"openshell",cpu:1,memoryMB:512,sharing:"shared",gitAccess:"github-read@1"};
  const grants=runtimeProfileGrants(profile);
  assert.deepEqual(grants,{files:[],network:["git:github-read@1"],credentials:["git:github-read@1"],cpu:1,memoryMB:512});
  assert.deepEqual(sandboxWire({mode:"new",profile:profile.id,...grants}),{mode:"new",profile:"git@1",files:[],network:["git:github-read@1"],credentials:["git:github-read@1"],cpu:1,memory_mb:512});
  grants.network.push("changed");assert.deepEqual(runtimeProfileGrants(profile).network,["git:github-read@1"]);
  assert.deepEqual(runtimeProfileGrants({...profile,gitAccess:undefined}).credentials,[]);
  assert.throws(()=>runtimeProfileGrants({...profile,gitAccess:"raw-key"}),/versioned/);
  assert.throws(()=>runtimeProfileGrants({...profile,gitAccess:null}),/versioned/);
});
test("sandbox selection serializes requested scope without inventing credentials or endpoints",()=>{
  assert.equal(sandboxWire(undefined),undefined);
  assert.deepEqual(sandboxWire({mode:"team"}),{mode:"team"});
  assert.deepEqual(sandboxWire({mode:"member",agentId:"builder-one"}),{mode:"member",agent_id:"builder-one"});
  assert.deepEqual(sandboxWire({mode:"new",profile:"small",cpu:2,memoryMB:1024}),{mode:"new",profile:"small",files:[],network:[],credentials:[],cpu:2,memory_mb:1024});
  assert.throws(()=>sandboxWire({mode:"new",profile:"small",endpoint:"https://other"}),/Unsupported/);
  assert.throws(()=>sandboxWire({mode:"team",credentials:["key"]}),/Unsupported/);
  // Explicit malformed values reach the Rust validator; they never turn into
  // default resources or grants through JavaScript null-coalescing.
  assert.equal(sandboxWire({mode:"new",profile:"small",memoryMB:null}).memory_mb,null);
});

test("service delegation preserves exact placement/profile requests and rejects unknown grants",()=>{
  assert.deepEqual(serviceDelegationWire({tools:[],placements:["new"],profiles:{"small@1":{cpu:1,memoryMB:512}}}),{tools:[],models:[],placements:["new"],profiles:{"small@1":{files:[],network:[],credentials:[],cpu:1,memory_mb:512}}});
  assert.throws(()=>serviceDelegationWire({tools:[],profiles:{p:{cpu:1,memoryMB:512,endpoint:"elsewhere"}}}),/Unsupported/);
  assert.throws(()=>serviceDelegationWire({tools:[],allowAll:true}),/Unsupported/);
  assert.throws(()=>serviceDelegationWire({tools:[],placements:null}),/Invalid/);
  assert.throws(()=>serviceDelegationWire({tools:[],profiles:null}),/Invalid/);
});
