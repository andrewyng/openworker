import test from "node:test";
import assert from "node:assert/strict";
import {sandboxWire} from "../dist/team-placement.js";
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
