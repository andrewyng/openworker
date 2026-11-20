// Run through the website_builder_app Rust fixture and verify_website_builder.py.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { randomBytes } from "node:crypto";
import { access, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const barrier = process.env.OW_WEBSITE_BARRIER;
const tokens = { alice: randomBytes(32).toString("hex"), bob: randomBytes(32).toString("hex") };
const site = (heading, description) => `<!doctype html><html lang="en"><meta charset="utf-8"><title>${heading}</title><main><h1>${heading}</h1><p>${description}</p></main></html>`;
const alicePage = site("Alice’s garden", "Seeds, planting dates and the first harvest.");
const bobPage = site("Bob’s studio", "Recordings and rehearsal notes.");
const edited = alicePage.replace("<h1>Alice’s garden</h1>", "<h1>Alice’s autumn garden</h1>");
const read = (path = "index.html") => ({ name: "read_file", arguments: { path } });
const write = (path, content) => ({ name: "write_file", arguments: { path, content } });
const list = { name: "list_files", arguments: {} };
let child, exited, base;
let startups = [];
async function start() {
  child = spawn(process.execPath, [fileURLToPath(new URL("../examples/website-builder/server.mjs", import.meta.url))], {
    stdio: ["ignore","pipe","inherit"],
    env: { ...process.env, PORT:"0", OW_SERVICE_NAMESPACE:"test",
      WEBSITE_ALICE_TOKEN:tokens.alice, WEBSITE_BOB_TOKEN:tokens.bob },
  });
  exited = once(child, "exit");
  let stdout = "";
  for await (const chunk of child.stdout.iterator({destroyOnReturn:false})) {
    stdout += chunk;
    const match = /Website builder listening at (http:\/\/127\.0\.0\.1:\d+)\n/.exec(stdout);
    if (match) { base = match[1]; startups.push(child.pid); return; }
  }
  throw new Error("Website builder exited before listening");
}
async function stop() {
  if (!child) return;
  child.kill("SIGTERM");
  await exited;
  child = null;
}
async function request(user, path, body) {
  const res = await fetch(base+path, {
    method:body ? "POST" : "GET",
    headers:{Authorization:`Bearer ${tokens[user] ?? "invalid"}`, "content-type":"application/json"},
    ...(body && {body:JSON.stringify(body)}),
  });
  return [res.status, await res.json()];
}
async function run(user, project, mode, calls, expectedStatus = "completed") {
  const [status, data] = await request(user, `/projects/${project}/runs`, {
    mode, brief:"TEST_RESOURCE_GRANTS "+JSON.stringify(calls),
  });
  assert.equal(status, 200, JSON.stringify(data));
  assert.equal(data.status, expectedStatus, JSON.stringify(data));
  assert.deepEqual(data.tools.map(t=>t.name),calls.map(c=>c.name));
  return data;
}
async function checkpoint(name) {
  await writeFile(join(barrier, name), "");
  for (let i=0; i<1200; i++) {
    try { await access(join(barrier,name+".ok")); return; }
    catch(err) { if(err.code!=="ENOENT") throw err; }
    await delay(100);
  }
  throw new Error("Timed out waiting for physical check: "+name);
}
try {
  await start();
  assert.match(await (await fetch(base)).text(), /Website builder/);
  assert.equal((await request("unknown","/projects"))[0],401);
  assert.deepEqual((await request("alice","/projects"))[1].map(p=>p.id),["garden"]);
  assert.deepEqual((await request("bob","/projects"))[1].map(p=>p.id),["studio"]);
  const dayOne = await run("alice","garden","build",[
    write("index.html",alicePage),write("planting.txt","Alice’s private planting dates"),read(),
  ]);
  assert.equal(dayOne.tools.at(-1).output.text,alicePage);
  await checkpoint("built");
  const shared = await run("alice","garden","review-shared",[read()]);
  assert.equal(shared.tools[0].output.text,alicePage);
  await checkpoint("shared");
  const separate = await run("alice","garden","review-separate",[read()]);
  assert.equal(separate.tools[0].output.text,alicePage);
  await checkpoint("separate");
  assert.equal((await request("bob","/projects/garden/runs",{mode:"review-shared",brief:"Read Alice’s page"}))[0],404);
  assert.equal((await request("bob","/projects/studio/runs",{mode:"build",brief:"Read",dataId:"alice"}))[0],400);
  const bob = await run("bob","studio","build",[list,write("index.html",bobPage),list,read()]);
  assert.deepEqual(bob.tools[0].output.files,[]);
  assert.deepEqual(bob.tools[2].output.files,["index.html"]);
  assert.equal(bob.tools[3].output.text,bobPage);
  const denied = await run("bob","studio","build",[read("planting.txt")],"failed");
  assert.notEqual(denied.tools[0].status,"finished");
  assert.ok(!JSON.stringify(denied).includes("Alice’s private planting dates"));
  await checkpoint("bob");
  await stop();
  await checkpoint("app-stopped");
  await start();
  assert.notEqual(startups[0],startups[1]);
  const tomorrow = await run("alice","garden","build",[read(),{
    name:"edit_file",arguments:{path:"index.html",old_text:"<h1>Alice’s garden</h1>",new_text:"<h1>Alice’s autumn garden</h1>"},
  },read(),list]);
  assert.notEqual(dayOne.sessionId,tomorrow.sessionId);
  assert.equal(tomorrow.tools[0].output.text,alicePage);
  assert.equal(tomorrow.tools[1].output.edited,true);
  assert.equal(tomorrow.tools[2].output.text,edited);
  assert.deepEqual(tomorrow.tools[3].output.files,["index.html","planting.txt"]);
  const bobAgain = await run("bob","studio","review-shared",[read(),list]);
  assert.equal(bobAgain.tools[0].output.text,bobPage);
  assert.deepEqual(bobAgain.tools[1].output.files,["index.html"]);
  await checkpoint("edited");
  console.log("PASS: both reviewer modes, distinct app processes and sessions, recovered edit, project authorization and isolated files.");
} finally { await stop(); }
