import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { start, allowed } from "../server.mjs";
const token = "test-only-token-".repeat(4);
test("strict route and query boundary", () => {
  for (const p of [
    "/v1/sessions?limit=50",
    "/v1/sessions/s_1/events?after=10",
    "/v1/releases",
    "/v1/profiles/a@1",
  ])
    assert(allowed("GET", p), p);
  for (const p of [
    "/auth/me",
    "/v1/sessions/../credentials",
    "/v1/sessions?limit=999",
    "/v1/sessions?limit=1&limit=2",
    "/v1/configuration?after=1",
    "/v1/sessions/s%2fsecret",
    "//evil/v1/sessions",
  ])
    assert(!allowed("GET", p), p);
  assert(!allowed("DELETE", "/v1/sessions/s_1"));
  assert(!allowed("POST", "/v1/sessions/s_1/events"));
  assert(allowed("PUT", "/v1/configuration"));
});
test("browser authority, expiry, logout, CSRF, and no mutation replay", async () => {
  let rejectBody = false;
  let revoked = false,
    writes = 0,
    receivedKey,
    fail = false;
  const upstream = http.createServer(async (req, res) => {
    assert.equal(req.headers.origin, undefined);
    assert.equal(req.headers.authorization, "Bearer " + token);
    assert.equal(req.headers["x-openworker-namespace"], "test");
    res.setHeader("x-openworker-service-id", "ui-test");
    res.setHeader("x-openworker-namespace", "test");
    res.setHeader("Content-Type", "application/json");
    if (revoked) {
      res.writeHead(401).end("{}");
      return;
    }
    if (req.url === "/v1/auth/me") {
      res.end(
        JSON.stringify({
          principal: "alice",
          role: "operator",
          scope: { kind: "owned" },
          expires_at: Math.floor(Date.now() / 1000) + 300,
        }),
      );
      return;
    }
    if (req.url === "/v1/capabilities") {
      res.end(
        JSON.stringify({
          apiVersion: "1",
          serviceId: "ui-test",
          namespace: "test",
        }),
      );
      return;
    }
    if (rejectBody) {
      res.writeHead(422, { "Content-Type": "text/plain" }).end("Bad input");
      return;
    }
    if (req.method === "POST") {
      writes++;
      receivedKey = req.headers["idempotency-key"];
      if (fail) {
        req.socket.destroy();
        return;
      }
    }
    res.end('{"sessions":[]}');
  });
  await new Promise((r) => upstream.listen(0, "127.0.0.1", r));
  const ui = await start({
    serviceUrl: `http://127.0.0.1:${upstream.address().port}`,
    namespace: "test",
    serviceId: "ui-test",
    port: 0,
  });
  try {
    const request = (path, options = {}) => fetch(ui.url + path, options);
    assert.equal((await request("/session")).status, 401);
    assert.equal(
      (
        await request("/login", {
          method: "POST",
          headers: {
            Origin: "https://evil.test",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ token }),
        })
      ).status,
      403,
    );
    const login = await request("/login", {
      method: "POST",
      headers: { Origin: ui.url, "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie");
    assert(cookie.includes("HttpOnly"));
    assert(cookie.includes("SameSite=Strict"));
    assert(!cookie.includes(token));
    const { csrf } = await login.json();
    const headers = {
      Cookie: cookie.split(";")[0],
      Origin: ui.url,
      "Content-Type": "application/json",
      "Idempotency-Key": "write-1",
    };
    assert.equal(
      (
        await request("/api/v1/submissions", {
          method: "POST",
          headers,
          body: "{}",
        })
      ).status,
      403,
    );
    assert.equal(writes, 0);
    headers["X-CSRF-Token"] = csrf;
    assert.equal(
      (
        await request("/api/v1/submissions", {
          method: "POST",
          headers,
          body: "{}",
        })
      ).status,
      200,
    );
    assert.equal(receivedKey, "write-1");
    rejectBody = true;
    const rejected = await request("/api/v1/submissions", {
      method: "POST",
      headers,
      body: "{}",
    });
    assert.equal(rejected.status, 422);
    assert.equal((await rejected.json()).error, "request_rejected");
    rejectBody = false;
    fail = true;
    const lost = await request("/api/v1/submissions", {
      method: "POST",
      headers: { ...headers, "Idempotency-Key": "write-2" },
      body: "{}",
    });
    assert.equal(lost.status, 502);
    assert.equal((await lost.json()).error, "outcome_unknown");
    assert.equal(writes, 2);
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(writes, 2, "No automatic retry");
    assert.equal(
      (
        await request("/api/v1/sessions", {
          headers: { Cookie: headers.Cookie, Origin: "https://evil.test" },
        })
      ).status,
      403,
    );
    revoked = true;
    assert.equal(
      (
        await request("/api/v1/sessions", {
          headers: { Cookie: headers.Cookie },
        })
      ).status,
      401,
    );
    assert.equal(
      (await request("/session", { headers: { Cookie: headers.Cookie } }))
        .status,
      401,
    );
    revoked = false;
    const again = await request("/login", {
      method: "POST",
      headers: { Origin: ui.url, "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const h = {
      Cookie: again.headers.get("set-cookie").split(";")[0],
      Origin: ui.url,
      "X-CSRF-Token": (await again.json()).csrf,
    };
    assert.equal(
      (await request("/logout", { method: "POST", headers: h })).status,
      200,
    );
    assert.equal(
      (await request("/api/v1/sessions", { headers: h })).status,
      401,
    );
    const html = await request("/");
    assert(
      html.headers
        .get("content-security-policy")
        .includes("frame-ancestors 'none'"),
    );
    assert.equal(html.headers.get("cache-control"), "no-store");
  } finally {
    await ui.close();
    upstream.closeAllConnections();
    await new Promise((r) => upstream.close(r));
  }
});
test("browser session expires independently of the provider credential", async () => {
  const upstream = http.createServer((req, res) => {
    res.setHeader("x-openworker-service-id", "ui-test");
    res.setHeader("x-openworker-namespace", "test");
    res.setHeader("Content-Type", "application/json");
    res.end(
      JSON.stringify(
        req.url.endsWith("me")
          ? { principal: "a", role: "observer", scope: { kind: "owned" } }
          : { apiVersion: "1", serviceId: "ui-test", namespace: "test" },
      ),
    );
  });
  await new Promise((r) => upstream.listen(0, "127.0.0.1", r));
  const app = await start({
    port: 0,
    sessionSeconds: 1,
    serviceUrl: `http://127.0.0.1:${upstream.address().port}`,
    serviceId: "ui-test",
    namespace: "test",
  });
  try {
    const r = await fetch(app.url + "/login", {
      method: "POST",
      headers: { Origin: app.url, "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    assert.equal(r.status, 200);
    const cookie = r.headers.get("set-cookie").split(";")[0];
    await new Promise((r) => setTimeout(r, 1100));
    assert.equal(
      (await fetch(app.url + "/session", { headers: { Cookie: cookie } }))
        .status,
      401,
    );
  } finally {
    await app.close();
    upstream.closeAllConnections();
    await new Promise((r) => upstream.close(r));
  }
});
