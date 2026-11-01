import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { Harness } from "../dist/index.js";
import { serviceFrames, serviceUrl } from "../dist/service.js";
const token = "test-only-credential-".repeat(3);
async function server(t, handler) {
  const s = http.createServer(handler);
  await new Promise((r) => s.listen(0, "127.0.0.1", r));
  t.after(() => {
    s.closeAllConnections();
    s.close();
  });
  return `http://127.0.0.1:${s.address().port}`;
}
function headers(res, id = "service-one") {
  res.setHeader("x-openworker-namespace", "test");
  res.setHeader("x-openworker-service-id", id);
}
function handshake(req, res, id = "service-one") {
  headers(res, id);
  if (req.url === "/v1/capabilities") {
    res.end(
      JSON.stringify({ apiVersion: "1", namespace: "test", serviceId: id }),
    );
    return true;
  }
  if (req.url === "/v1/auth/me") {
    res.end('{"principal":"tester"}');
    return true;
  }
  return false;
}
const options = (url) => ({
  url,
  namespace: "test",
  credentials: { getToken: async () => token },
});
test("profile registration carries a named Git grant and original idempotency key",async(t)=>{
  const profile={id:"git@1",provider:"openshell",cpu:1,memoryMB:512,sharing:"shared",gitAccess:"github-read@1"};
  const url=await server(t,(q,r)=>{
    if(handshake(q,r))return;
    assert.equal(q.url,"/v1/profiles");assert.equal(q.method,"POST");
    assert.equal(q.headers["idempotency-key"],"git-profile-original");
    let text="";q.on("data",c=>text+=c);q.on("end",()=>{
      const body=JSON.parse(text);assert.deepEqual(body,profile);headers(r);
      r.end(JSON.stringify({id:body.id,kind:"profile",digest:"digest",definition:body,installation:"fixture",activated:false}));
    });
  });
  const s=await Harness.connect(options(url));
  try{assert.equal((await s.registerRuntimeProfile(profile,{idempotencyKey:"git-profile-original"})).definition.gitAccess,"github-read@1");}
  finally{s.disconnect();}
});
test("URL validation rejects insecure remote endpoints and embedded secrets", () => {
  for (const u of [
    "http://example.org",
    "https://user:pass@host",
    "https://host/?secret=x",
    "https://host/#x",
  ])
    assert.throws(() => serviceUrl(u));
  assert.equal(serviceUrl("http://127.0.0.1:123/"), "http://127.0.0.1:123");
});
test("SSE handles split unicode, heartbeats, multiple frames and discards partial tail", async () => {
  const data = {
    event_id: 1,
    session_id: "s",
    type: "text",
    data: { text: "😀" },
  };
  const bytes = new TextEncoder().encode(
    `: heartbeat\r\n\r\nevent: progress\r\nid: 1\r\ndata: ${JSON.stringify(data)}\r\n\r\nevent: progress\nid: 2\ndata: {`,
  );
  const response = new Response(
    new ReadableStream({
      start(c) {
        for (const b of bytes) c.enqueue(Uint8Array.of(b));
        c.close();
      },
    }),
  );
  const result = [];
  for await (const f of serviceFrames(response)) result.push(f);
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].data, data);
});
test("SSE bounds frames and times out stalled reads", async () => {
  await assert.rejects(async () => {
    for await (const _ of serviceFrames(new Response("x".repeat(1048577)))) {
    }
  }, /event_too_large/);
  await assert.rejects(async () => {
    for await (const _ of serviceFrames(
      new Response(new ReadableStream()),
      15,
    )) {
    }
  }, /unavailable/);
});
test("identity mismatch on fallback endpoint fails closed", async (t) => {
  const a = await server(t, (q, r) => handshake(q, r));
  const b = await server(t, (q, r) => handshake(q, r, "different"));
  await assert.rejects(
    Harness.connect({ ...options(a), fallbackUrls: [b] }),
    /service_identity_mismatch/,
  );
});
test("refreshes rejected credentials once with same mutation key; never repeats unknown outcomes", async (t) => {
  let posts = 0;
  const reasons = [];
  const url = await server(t, (q, r) => {
    if (handshake(q, r)) return;
    if (q.method === "POST") {
      posts++;
      assert.equal(q.headers["idempotency-key"], "retained");
      if (posts === 1) {
        r.writeHead(401);
        r.end();
        return;
      }
      q.socket.destroy();
    }
  });
  const s = await Harness.connect({
    ...options(url),
    credentials: {
      getToken: async (reason) => {
        reasons.push(reason);
        return token;
      },
    },
  });
  t.after(() => s.disconnect());
  await assert.rejects(
    s.json("POST", "/unknown", {}, "retained"),
    (e) => e.code === "outcome_unknown" && e.idempotencyKey === "retained",
  );
  assert.equal(posts, 2);
  assert.equal(reasons.filter((r) => r === "rejected").length, 1);
});
test("reconnect replays cursor through other API, suppresses duplicates, disconnect does not cancel work", async (t) => {
  let first = true;
  const seen = [];
  let mutations = 0;
  const event = (n) =>
    `event: progress\nid: ${n}\ndata: ${JSON.stringify({ event_id: n, session_id: "s", turn_id: "t", type: "text", data: {} })}\n\n`;
  const handler = (q, r) => {
    if (handshake(q, r)) return;
    if (q.method !== "GET") mutations++;
    seen.push(q.headers["last-event-id"]);
    r.setHeader("content-type", "text/event-stream");
    if (first) {
      first = false;
      r.end(event(1));
    } else r.end(event(1) + event(2));
  };
  const a = await server(t, handler),
    b = await server(t, handler);
  const s = await Harness.connect({ ...options(a), fallbackUrls: [b] });
  t.after(() => s.disconnect());
  const events = [];
  for await (const e of s.events("s")) {
    events.push(e.event_id);
    if (e.event_id === 2) break;
  }
  s.disconnect();
  assert.deepEqual(events, [1, 2]);
  assert.deepEqual(seen, ["0", "1"]);
  assert.equal(mutations, 0);
  await assert.rejects(s.agent("builder"), /disconnected/);
});

test("provisioning wait transport errors retain operation identity and admission key", async (t) => {
  const url = await server(t, (q, r) => {
    if (handshake(q, r)) return;
    q.socket.destroy();
  });
  const s = await Harness.connect(options(url));
  t.after(() => s.disconnect());
  await assert.rejects(
    s.waitOperation("op_original", { idempotencyKey: "create-original" }),
    (e) =>
      e.code === "unavailable" &&
      e.operationId === "op_original" &&
      e.idempotencyKey === "create-original",
  );
});

test("combined submit preserves alias, limits and idempotency without two-step creation", async (t) => {
  const limits = {maxModelCalls:2,maxOutputTokens:2048,summaryTokens:256};
  let posts = 0;
  const url = await server(t, (q, r) => {
    headers(r);
    if (q.url === "/v1/capabilities") {
      r.end(
        JSON.stringify({
          apiVersion: "1",
          namespace: "test",
          serviceId: "service-one",
          features: { combinedSubmission: true },
        }),
      );
      return;
    }
    if (handshake(q, r)) return;
    assert.equal(q.url, "/v1/submissions");
    assert.equal(q.method, "POST");
    assert.equal(q.headers["idempotency-key"], "original");
    posts++;
    let body = "";
    q.on("data", (b) => (body += b));
    q.on("end", () => {
      assert.deepEqual(JSON.parse(body), {
        agent: "builder",
        text: "Build it",
        project: "game",
        limits,
      });
      r.end(
        JSON.stringify({
          operation: {
            id: "op-one",
            sessionId: "session-one",
            runId: "run-one",
            submissionId: "op-one",
          },
        }),
      );
    });
  });
  const service = await Harness.connect(options(url));
  const operation = await service.submit("builder", "Build it", {
    project: "game",
    idempotencyKey: "original",
    limits,
  });
  assert.equal(operation.runId, "run-one");
  assert.equal(posts, 1);
  await assert.rejects(
    service.submit("builder", "Build it", {
      environmentId: "env",
      runtimeProfile: "p",
    }),
    /conflicting_environment_options/,
  );
  assert.equal(posts, 1);
});

test("team delegation pins qualified tools and working-model pairs at service creation",async t=>{
  const bodies=[];
  const url=await server(t,async(q,r)=>{
    if(handshake(q,r))return;
    headers(r);
    if(q.url.startsWith("/v1/agents/")){r.end('{"id":"lead@1"}');return;}
    if(q.method==="GET"){r.end(JSON.stringify({service:{releaseId:"lead@1",runtimeProfile:"shared@1"},session:{environment:{environment_id:"env"}}}));return;}
    if(q.method==="POST"){
      let body="";for await(const chunk of q)body+=chunk;
      bodies.push(JSON.parse(body));
    }
    r.end('{}');
  });
  const service=await Harness.connect(options(url));t.after(()=>service.disconnect());
  const session=await service.resume({serviceId:"service-one",namespace:"test",sessionId:"lead",agent:"lead@1",runtimeProfile:"shared@1",environmentId:"env",project:"test"});
  await session.createTeam({agentTypes:{builder:"builder@1"},workspaceDefaults:{repository:"source",base:"a".repeat(40),agentTypes:["builder"]},delegation:{tools:["coding.read_file"],models:[{provider:"primary",name:"working"}]}});
  assert.deepEqual(bodies[0].workspaceDefaults,{repository:"source",base:"a".repeat(40),agent_types:["builder"]});
  assert.deepEqual(bodies[0].delegation,{tools:["coding.read_file"],models:[{connection:"primary",model:"working"}],placements:["team"],profiles:{}});
  assert.equal(bodies[0].agentId,"lead");
  await assert.rejects(session.createTeam({agentTypes:{builder:"builder@1"},delegation:{tools:["coding.read_file"],unknownPermission:true}}), /Unsupported team delegation/);
  await session.createTeam({agentTypes:{builder:"builder@1"},delegation:{tools:["coding.read_file"],placements:["team","new","member"],profiles:{"small@1":{cpu:1,memoryMB:512}}}});
  assert.deepEqual(bodies[1].delegation,{tools:["coding.read_file"],models:[],placements:["team","new","member"],profiles:{"small@1":{cpu:1,memory_mb:512,files:[],network:[],credentials:[]}}});
  assert.equal(bodies.length,2);
  assert(!JSON.stringify(bodies).includes(token));
});

test("coding defaults and submitted-source selection preserve independent checkout semantics on the wire",async(t)=>{
  const {ServiceSession}=await import("../dist/service.js");
  const requests=[];
  const url=await server(t,(q,r)=>{
    if(handshake(q,r))return;
    let text="";q.on("data",c=>text+=c);q.on("end",()=>{requests.push(JSON.parse(text));headers(r);r.end("{}");});
  });
  const service=await Harness.connect(options(url));
  try{
    const lead=new ServiceSession(service,{serviceId:"service-one",namespace:"test",sessionId:"lead",agent:"coder@1",environmentId:"env"});
    const team=await lead.createTeam({agentTypes:{builder:"coder@1"},coding:{source:{repository:"org/game",directory:"source",objectFormat:"sha1",commit:"a".repeat(40)},agentTypes:{builder:{source:"initial",sandbox:{mode:"new",profile:"small@1",memoryMB:512,cpu:1}}}}});
    assert.equal(requests[0].coding.source.object_format,"sha1");assert.equal(requests[0].coding.agent_types.builder.sandbox.memory_mb,512);assert.equal(requests[0].coding.agent_types.builder.sandbox.mode,"new");
    await team.spawn("verifier-one","builder","verify","Check exact commit",{source:{kind:"submission",id:"hs_original"}});
    assert.deepEqual(requests[1].arguments.source,{kind:"submission",id:"hs_original"});
    await team.submissions({after:"hs_original",limit:5});assert.deepEqual(requests[2],{operation:"team_list_submissions",arguments:{after:"hs_original",limit:5}});
  }finally{service.disconnect();}
});
