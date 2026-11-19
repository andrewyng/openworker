// Run by the workspace-reference PostgreSQL qualification.
import { writeFile, access } from "node:fs/promises";
import {join} from "node:path";
import assert from "node:assert/strict";
import { Service } from "../dist/index.js";
import { setTimeout as delay } from "node:timers/promises";

const svc = await Service.connect({
  url: process.env.OW_SERVICE_URL, namespace: "test",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});

async function tools(session, operations) {
  const run = await session.send("TEST_RESOURCE_GRANTS " + JSON.stringify(operations));
  const result = await run.result();
  assert.equal(result.status, "completed", JSON.stringify(result));
  const outputs = [];
  for await (const { event } of run.events()) {
    if (event.type === "tool_finished") {
      assert.equal(event.data.result.isError, false, JSON.stringify(event.data));
      outputs.push(event.data.result.structuredContent);
    }
  }
  assert.equal(outputs.length, operations.length);
  return outputs;
}

const read = {name:"read_file",arguments:{path:"index.html"}};
async function close(session) {
  for (let attempt=0; ; attempt++) {
    try { await session.close(); return; }
    catch (e) { if (e.status !== 409 || attempt >= 100) throw e; }
    await delay(100);
  }
}

try {
  const barrier=process.env.OW_K8S_BARRIER;
  const sites = await svc.workspaces.startProvider({name:"websites",options:{podProfile:"small",...(barrier ? {} : {accessToken:process.env.OW_WS_TEST_SECRET})}});
  const isolated = await svc.workspaces.startProvider({name:"research"});
  const [a,retry] = await Promise.all([sites.getOrCreate({key:"portfolio/builder"}), sites.getOrCreate({key:"portfolio/builder"})]);
  assert.deepEqual(a,retry);
  const other = await isolated.getOrCreate({key:"portfolio/builder"});
  assert.notEqual(other.dataId,a.dataId);
  await assert.rejects(isolated.getOrCreate({key:"foreign",dataId:a.dataId}));
  const b = await sites.getOrCreate({key:"portfolio/reviewer",dataId:a.dataId,options:{podProfile:"medium"}});
  assert.equal(a.dataId,b.dataId); assert.notEqual(a.workspaceId,b.workspaceId);
  const restricted = await Service.connect({
    url: process.env.OW_SERVICE_URL, namespace:"test", credentials:{tokenEnv:"OW_WS_RESTRICTED_TOKEN"},
  });
  try {
    await assert.rejects(restricted.workspaces.startProvider({name:"websites"}), {status:403});
    await assert.rejects(restricted.json("POST", "/v1/sessions", {agent:"builder", workspaceId:a.workspaceId}, "restricted-attach"), {status:403});
  } finally { restricted.disconnect(); }
  const unrelated = await sites.getOrCreate({key:"another-project"});
  await assert.rejects(sites.getOrCreate({key:"portfolio/builder",dataId:unrelated.dataId}),
    {code:"workspace_data_mismatch"});
  const builder = await svc.agent("builder");
  const reviewer = await svc.agent("reviewer");
  const main = await builder.newSession({workspaceId:a.workspaceId});
  const peer = await reviewer.newSession({workspaceId:a.workspaceId});
  const separate = await reviewer.newSession({workspaceId:b.workspaceId});
  await tools(main,[{name:"write_file",arguments:{path:"index.html",content:"Hello Alice"}}]);
  if (barrier) {
    await assert.rejects(isolated.getOrCreate({key:"extra",dataId:other.dataId}),{code:"workspace_shared_mount_unsupported"});
    await writeFile(join(barrier,"identities.json"), JSON.stringify({builder:a,reviewer:b,independent:other}));
    for (let i=0; ; i++) {
      try { await access(join(barrier,"continue")); break; }
      catch (e) { if (e.code !== "ENOENT" || i >= 1800) throw e; }
      await delay(100);
    }
  }
  // Resume the same session after replacing both worker instances.
  const dir=process.env.OW_WS_RESTART_DIR;
  await writeFile(join(dir,"restart"), "");
  for (let i=0; ; i++) {
    try { await access(join(dir,"restarted")); break; }
    catch (e) { if (e.code !== "ENOENT" || i >= 200) throw e; }
    await delay(100);
  }
  assert.equal((await tools(peer,[read]))[0].text,"Hello Alice");
  if (barrier) {
    await writeFile(join(barrier,"read-back"), "Hello Alice");
    for (let i=0; ; i++) {
      try { await access(join(barrier,"verified")); break; }
      catch (e) { if (e.code !== "ENOENT" || i >= 900) throw e; }
      await delay(100);
    }
  }
  // OpenWorker has closed the session, but the first provider acknowledgement
  // was lost. Replay the request and check that the sibling keeps its access.
  if (!barrier) await assert.rejects(close(peer), {status:503});
  await close(peer);
  await tools(main,[{name:"edit_file",arguments:{path:"index.html",old_text:"Hello",new_text:"Welcome"}}]);
  const resumed = await svc.resume(separate.reference());
  assert.equal((await tools(resumed,[read]))[0].text,"Welcome Alice");
  await assert.rejects(sites.stopWorkspace(a.workspaceId), {code:"workspace_in_use"});
  await close(main);
  await sites.stopWorkspace(a.workspaceId);
  await assert.rejects(sites.deleteData(a.dataId), {code:"workspace_in_use"});
  assert.deepEqual((await tools(separate,[{name:"list_files",arguments:{}}]))[0].files,["index.html"]);
  await close(separate);
  await sites.stopWorkspace(b.workspaceId);
  const reopened = await sites.getOrCreate({key:"portfolio/builder"});
  assert.equal(reopened.dataId, a.dataId);
  assert.notEqual(reopened.workspaceId, a.workspaceId);
  const tomorrow = await builder.newSession({workspaceId:reopened.workspaceId});
  assert.equal((await tools(tomorrow,[read]))[0].text, "Welcome Alice");
  await close(tomorrow);
  await sites.stopWorkspace(reopened.workspaceId);
  await sites.deleteData(a.dataId);
  await assert.rejects(sites.getOrCreate({key:"stale",dataId:a.dataId}), {code:"workspace_not_found"});
  await isolated.stopWorkspace(other.workspaceId);
  await isolated.deleteData(other.dataId);
  await sites.stopWorkspace(unrelated.workspaceId);
  await sites.deleteData(unrelated.dataId);
  console.log("PASS: actual SDK sessions, PostgreSQL admissions, worker file tools, both sharing arrangements and close/resume");
} finally { svc.disconnect(); }
