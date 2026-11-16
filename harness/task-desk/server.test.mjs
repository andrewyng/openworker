import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { start, allowed } from "./server.mjs";

test("route surface excludes administration, arbitrary uploads, traversal and URL injection", () => {
  for (const path of [
    "/credentials",
    "/security/audit",
    "//evil/sessions",
    "/sessions/s/../credentials",
    "/sessions/%2e%2e",
    "/sessions/s?token=x",
    "/sessions/s/events?after=-1",
  ])
    assert.equal(allowed("GET", path), false, path);
  assert.ok(allowed("GET", "/sessions/s/events?after=42"));
  assert.ok(allowed("GET", "/sessions?limit=100&after=s"));
  assert.ok(allowed("POST", "/sessions/s/controls"));
  assert.equal(allowed("PUT", "/sessions/s/artifacts/a/content"), false);
});
async function fixture(t, role = "operator", scope = { kind: "owned" }) {
  const folder = await mkdtemp(join(tmpdir(), "ow-taskdesk-test-"));
  const key = randomBytes(32).toString("hex"),
    token = randomBytes(32).toString("hex"),
    seen = [];
  const api = http.createServer(async (req, res) => {
    assert.equal(req.headers.authorization, "Bearer " + token);
    assert.equal(req.headers.origin, undefined);
    let body = "";
    for await (const c of req) body += c;
    seen.push({ path: req.url, method: req.method, body });
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/auth/me")
      return res.end(
        JSON.stringify({ role, scope, environments: ["env_test"] }),
      );
    if (req.url === "/sessions/s/events?after=12") {
      res.setHeader("Content-Type", "text/event-stream");
      res.write(
        'id: 13\nevent: progress\ndata: {"event_id":13,"type":"tool_finished"}\n\n',
      );
      return;
    }
    if (req.url === "/sessions/s/turns") {
      req.socket.destroy();
      return;
    }
    if (req.url === "/sessions/s")
      return res.end(
        JSON.stringify({
          turn: { id: "turn1", status: "completed" },
          messages: [
            {
              role: "assistant",
              content: [
                { type: "output_text", text: "# Finished\nA useful result." },
              ],
            },
          ],
        }),
      );
    if (req.url === "/sessions/s/artifacts" && req.method === "POST")
      return res.end(JSON.stringify({ id: "artifact1", status: "pending" }));
    if (req.url === "/sessions/s/artifacts/artifact1/content")
      return res.end(req.method === "GET" ? "<script>evil</script>" : "{}");
    res.end(
      JSON.stringify(
        req.url === "/sessions" && req.method === "POST"
          ? { session_id: "s" }
          : { ok: true },
      ),
    );
  });
  await new Promise((r) => api.listen(0, "127.0.0.1", r));
  await writeFile(join(folder, "api"), token, { mode: 0o600 });
  await writeFile(join(folder, "access"), key, { mode: 0o600 });
  const config = {
    apis: [`http://127.0.0.1:${api.address().port}`],
    api_token_file: join(folder, "api"),
    access_key_file: join(folder, "access"),
    environment_id: "env_test",
    coworker: "assistant-example",
    workspace: "/sandbox",
    port: 0,
  };
  t.after(async () => {
    api.closeAllConnections();
    await new Promise((r) => api.close(r));
    await rm(folder, { recursive: true });
  });
  return { config, key, token, seen };
}
async function appFixture(t) {
  const f = await fixture(t),
    app = await start(f.config);
  t.after(() => app.close());
  const login = await fetch(app.url + "/login", {
    method: "POST",
    headers: { Origin: app.url, "Content-Type": "application/json" },
    body: JSON.stringify({ key: f.key }),
  });
  assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie").split(";")[0];
  return {
    ...f,
    ...app,
    cookie,
    request: (path, method = "GET", body, extra = {}) =>
      fetch(app.url + path, {
        method,
        headers: {
          Cookie: cookie,
          ...(method === "GET"
            ? {}
            : { Origin: app.url, "Content-Type": "application/json" }),
          ...extra,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
  };
}
test("reject administrator and broad operator keys at startup", async (t) => {
  for (const [role, scope] of [
    ["administrator", { kind: "all" }],
    ["operator", { kind: "all" }],
  ]) {
    const f = await fixture(t, role, scope);
    await assert.rejects(start(f.config), /owned-scope/);
  }
});
test("requires login, correct host/origin and distinct keys; never returns operator token", async (t) => {
  const a = await appFixture(t);
  assert.equal((await fetch(a.url + "/api/sessions")).status, 401);
  assert.equal(
    (
      await a.request("/api/sessions", "GET", undefined, {
        Origin: "https://evil.test",
      })
    ).status,
    403,
  );
  const wrongHost = await new Promise((resolve, reject) => {
    const q = http.get(
      a.url + "/api/sessions",
      { headers: { Host: "evil.test", Cookie: a.cookie } },
      (r) => {
        r.resume();
        resolve(r.statusCode);
      },
    );
    q.on("error", reject);
  });
  assert.equal(wrongHost, 403);
  assert.equal(
    (
      await fetch(a.url + "/api/sessions", {
        method: "POST",
        headers: { Cookie: a.cookie, "Content-Type": "application/json" },
        body: "{}",
      })
    ).status,
    403,
  );
  assert.equal((await a.request("/api/credentials")).status, 404);
  const page = await (await fetch(a.url)).text();
  assert.ok(!page.includes(a.token));
  await assert.rejects(
    start({ ...a.config, access_key_file: a.config.api_token_file }),
    /Distinct/,
  );
});
test("session parameters are pinned by app, not supplied by browser", async (t) => {
  const a = await appFixture(t);
  assert.equal(
    (
      await a.request("/api/sessions", "POST", {
        request_id: "new",
        name: "New task",
        parameters: { environment_id: "evil" },
      })
    ).status,
    400,
  );
  assert.equal(
    (
      await a.request("/api/sessions", "POST", {
        request_id: "new",
        name: "New task",
      })
    ).status,
    200,
  );
  assert.deepEqual(JSON.parse(a.seen.at(-1).body), {
    request_id: "new",
    parameters: {
      name: "New task",
      coworker: "assistant-example",
      workspace: "/sandbox",
      environment_id: "env_test",
    },
  });
});
test("unknown mutation outcome is reported without automatic replay", async (t) => {
  const a = await appFixture(t);
  const r = await a.request("/api/sessions/s/turns", "POST", {
    submission_id: "stable-id",
    text: "hello",
  });
  assert.equal(r.status, 503);
  assert.equal((await r.json()).error, "outcome_unknown_retry_same_request");
  assert.equal(a.seen.filter((x) => x.path === "/sessions/s/turns").length, 1);
});
test("export binds completed turn, hashes actual response, forces inert download", async (t) => {
  const a = await appFixture(t);
  assert.equal(
    (await a.request("/api/sessions/s/result", "POST", { turn_id: "other" }))
      .status,
    409,
  );
  const r = await a.request("/api/sessions/s/result", "POST", {
    turn_id: "turn1",
  });
  assert.equal(r.status, 200);
  const reg = JSON.parse(
    a.seen.find((x) => x.path === "/sessions/s/artifacts").body,
  );
  assert.equal(reg.request_id, "result-turn1");
  assert.equal(reg.size, Buffer.byteLength("# Finished\nA useful result.\n"));
  assert.equal(
    a.seen.find((x) => x.method === "PUT").body,
    "# Finished\nA useful result.\n",
  );
  const download = await a.request(
    "/api/sessions/s/artifacts/artifact1/content",
  );
  assert.match(download.headers.get("content-disposition"), /attachment/);
  assert.equal(
    download.headers.get("content-type"),
    "application/octet-stream",
  );
});
test("SSE preserves replay cursor and logout terminates the stream", async (t) => {
  const a = await appFixture(t);
  const r = await a.request(
    "/api/sessions/s/events?after=0",
    "GET",
    undefined,
    { "Last-Event-ID": "12" },
  );
  assert.match(r.headers.get("content-type"), /event-stream/);
  const reader = r.body.getReader();
  assert.match(Buffer.from((await reader.read()).value).toString(), /id: 13/);
  assert.equal((await a.request("/logout", "POST", {})).status, 200);
  try {
    const next = await reader.read();
    assert.ok(next.done);
  } catch (e) {
    assert.match(e.message, /terminated/);
  }
  assert.equal((await a.request("/api/sessions")).status, 401);
});
