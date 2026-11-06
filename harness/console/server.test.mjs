import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { start, allowed } from "./server.mjs";
test("read-only allowlist excludes mutations, traversal, arbitrary proxies and query injection", () => {
  for (const p of [
    "/sessions",
    "/sessions?limit=50&after=s_one",
    "/sessions/s_one",
    "/sessions/s_one/events?after=12",
    "/sessions/s_one/artifacts/a_one/content",
    "/readyz",
  ])
    assert.ok(allowed(p), p);
  for (const p of [
    "/sessions/s_one/controls",
    "/sessions/s_one/turns",
    "/sessions/s_one/artifacts/a/reconcile",
    "/sessions?limit=999",
    "/sessions?url=http://bad",
    "/sessions/../readyz",
    "/%2e%2e/readyz",
    "//bad/sessions",
    "//[",
    "/sessions/s_one?secret=yes",
    "/sessions/s_one/events?after=-1",
  ])
    assert.equal(allowed(p), false, p);
});
test("authenticated observer proxy, failover, no browser credential leak or mutation route", async () => {
  const root = await mkdtemp(join(tmpdir(), "ow-console-"));
  const key = "b".repeat(64),
    token = "a".repeat(64);
  let calls = 0;
  await writeFile(join(root, "key"), key);
  await writeFile(join(root, "api"), token);
  const upstream = http.createServer((req, res) => {
    if (req.url !== "/auth/me") calls++;
    assert.equal(req.headers.authorization, "Bearer " + token);
    assert.equal(req.method, "GET");
    assert.equal(req.headers.cookie, undefined);
    assert.equal(req.headers.origin, undefined);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify(
        req.url === "/auth/me"
          ? { role: "observer", credential_id: "key_test" }
          : { sessions: [], sentinel: "<script>secret</script>" },
      ),
    );
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const app = await start({
    port: 0,
    apis: ["http://127.0.0.1:1", `http://127.0.0.1:${upstream.address().port}`],
    api_token_file: join(root, "api"),
    access_key_file: join(root, "key"),
  });
  try {
    const page = await fetch(app.url);
    assert.equal(page.status, 200);
    assert.ok(!(await page.text()).includes(token));
    assert.equal((await fetch(app.url + "/api/sessions")).status, 401);
    assert.equal(
      (
        await fetch(app.url + "/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key }),
        })
      ).status,
      403,
    );
    const login = await fetch(app.url + "/login", {
      method: "POST",
      headers: { Origin: app.url, "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie").split(";")[0];
    assert.ok(login.headers.get("set-cookie").includes("HttpOnly"));
    assert.ok(login.headers.get("set-cookie").includes("SameSite=Strict"));
    const result = await fetch(app.url + "/api/sessions", {
      headers: { Cookie: cookie },
    });
    assert.equal(result.status, 200);
    assert.equal((await result.json()).sentinel, "<script>secret</script>");
    assert.ok(
      result.headers
        .get("content-security-policy")
        .includes("frame-ancestors 'none'"),
    );
    assert.equal(calls, 1);
    for (const [method, path] of [
      ["POST", "/api/sessions"],
      ["DELETE", "/api/sessions/s_one"],
      ["GET", "/api/sessions/s_one/controls"],
    ]) {
      assert.ok(
        [404, 405].includes(
          (await fetch(app.url + path, { method, headers: { Cookie: cookie } }))
            .status,
        ),
      );
    }
    assert.equal(calls, 1);
    assert.equal(
      (
        await fetch(app.url + "/api/sessions", {
          headers: { Cookie: cookie, Origin: "https://evil.example" },
        })
      ).status,
      403,
    );
    const rebinding = await new Promise((resolve, reject) => {
      http
        .get(
          app.url + "/api/sessions",
          { headers: { Cookie: cookie, Host: "evil.example" } },
          (r) => {
            r.resume();
            resolve(r.statusCode);
          },
        )
        .on("error", reject);
    });
    assert.equal(rebinding, 403);
    assert.equal(
      (
        await fetch(app.url + "/logout", {
          method: "POST",
          headers: { Cookie: cookie, Origin: app.url },
        })
      ).status,
      200,
    );
    assert.equal(
      (await fetch(app.url + "/api/sessions", { headers: { Cookie: cookie } }))
        .status,
      401,
    );
  } finally {
    await app.close();
    upstream.closeAllConnections();
    await new Promise((r) => upstream.close(r));
    await rm(root, { recursive: true });
  }
});
test("console refuses administrator/operator credentials before opening a listener", async () => {
  const root = await mkdtemp(join(tmpdir(), "ow-console-role-"));
  let role = "administrator";
  const upstream = http.createServer((req, res) => {
    assert.equal(req.url, "/auth/me");
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ role, credential_id: "key_test" }));
  });
  try {
    await writeFile(join(root, "key"), "a".repeat(64));
    await writeFile(join(root, "token"), "b".repeat(64));
    upstream.listen(0, "127.0.0.1");
    await once(upstream, "listening");
    const config = {
      port: 0,
      apis: [`http://127.0.0.1:${upstream.address().port}`],
      api_token_file: join(root, "token"),
      access_key_file: join(root, "key"),
    };
    for (role of ["administrator", "operator"]) {
      await assert.rejects(start(config), /observer credential/);
    }
  } finally {
    upstream.closeAllConnections();
    await new Promise((r) => upstream.close(r));
    await rm(root, { recursive: true });
  }
});
