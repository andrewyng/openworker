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
