// Optional read-only browser adapter. No database driver, tool execution or writes.
import http from "node:http";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
const here = dirname(fileURLToPath(import.meta.url));
function equal(a, b) {
  const x = Buffer.from(a || ""),
    y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
function origin(value) {
  const u = new URL(value);
  if (
    u.protocol !== "http:" ||
    !["127.0.0.1", "[::1]"].includes(u.hostname) ||
    u.username ||
    u.password ||
    u.pathname !== "/" ||
    u.search ||
    u.hash
  )
    throw Error("Console requires explicit loopback API origins");
  return u.origin;
}
export function allowed(path) {
  if (
    typeof path !== "string" ||
    !/^\/[A-Za-z0-9_/?=&-]*$/.test(path) ||
    path.startsWith("//")
  )
    return false;
  const u = new URL(path, "http://local");
  const id = "[A-Za-z0-9_-]{1,80}";
  if (u.origin !== "http://local" || u.pathname + u.search !== path)
    return false;
  if (u.pathname === "/readyz") return !u.search;
  if (u.pathname === "/sessions")
    return [...u.searchParams].every(
      ([k, v]) =>
        (k === "after" && new RegExp(`^${id}$`).test(v)) ||
        (k === "limit" && /^\d{1,3}$/.test(v) && +v >= 1 && +v <= 100),
    );
  if (
    new RegExp(
      `^/sessions/${id}(/artifacts(/[A-Za-z0-9_-]{1,40}(/content)?)?)?$`,
    ).test(u.pathname)
  )
    return !u.search;
  if (new RegExp(`^/sessions/${id}/events$`).test(u.pathname))
    return [...u.searchParams].every(
      ([k, v]) => k === "after" && /^\d{1,16}$/.test(v),
    );
  return false;
}
export async function start(config) {
  const port = config.port ?? 54340;
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    throw Error("Invalid console port");
  const apis = config.apis.map(origin);
  if (apis.length < 1 || apis.length > 4) throw Error("Provide 1–4 APIs");
  const grafana = config.grafana_url
    ? origin(new URL(config.grafana_url).origin) +
      new URL(config.grafana_url).pathname
    : null;
  const token = (await readFile(config.api_token_file, "utf8")).trim(),
    access = (await readFile(config.access_key_file, "utf8")).trim();
  if (
    !/^[A-Za-z0-9_.-]{32,256}$/.test(token) ||
    !/^[A-Za-z0-9_.-]{32,256}$/.test(access) ||
    equal(token, access)
  )
    throw Error("Distinct private API and console credentials required");
  // A proxy allowlist is defense in depth, not the API authorization boundary.
  let identity;
  for (const api of apis) {
    let response;
    try {
      response = await fetch(api + "/auth/me", {
        headers: { Authorization: "Bearer " + token },
        redirect: "error",
        signal: AbortSignal.timeout(3000),
      });
    } catch {
      continue;
    }
    if (!response.ok) throw Error("Console API credential was rejected");
    const current = await response.json();
    if (
      current.role !== "observer" ||
      !current.credential_id ||
      (identity && current.credential_id !== identity.credential_id)
    )
      throw Error("Console requires the same observer credential on every API");
    identity = current;
  }
  if (!identity)
    throw Error("No API available to verify console observer credential");
  const assets = new Map(
    await Promise.all(
      [
        ["/", "index.html", "text/html"],
        ["/app.js", "app.js", "text/javascript"],
        ["/style.css", "style.css", "text/css"],
      ].map(async ([path, file, type]) => [
        path,
        { type, body: await readFile(join(here, "public", file)) },
      ]),
    ),
  );
  const sessions = new Map();
  let attempts = [],
    active = 0,
    actualPort = port;
  const server = http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    );
    const send = (status, value) => {
      if (res.headersSent || res.destroyed) {
        res.destroy();
        return;
      }
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(value));
    };
    const own = `http://127.0.0.1:${actualPort}`;
    if (
      req.headers.host !== `127.0.0.1:${actualPort}` ||
      (req.headers.origin && req.headers.origin !== own) ||
      ["cross-site", "same-site"].includes(req.headers["sec-fetch-site"])
    ) {
      send(403, { error: "origin_denied" });
      return;
    }
    if (req.method === "GET" && assets.has(req.url)) {
      const asset = assets.get(req.url);
      res.writeHead(200, { "Content-Type": asset.type });
      res.end(asset.body);
      return;
    }
    if (req.url === "/login" && req.method === "POST") {
      if (
        req.headers.origin !== own ||
        req.headers["content-type"] !== "application/json"
      ) {
        send(403, { error: "origin_required" });
        return;
      }
      attempts = attempts.filter((t) => Date.now() - t < 60000);
      if (attempts.length >= 10) {
        send(429, { error: "try_later" });
        return;
      }
      attempts.push(Date.now());
      let body = "";
      try {
        for await (const chunk of req) {
          body += chunk;
          if (body.length > 1024) {
            send(413, { error: "body_limit" });
            return;
          }
        }
        const value = JSON.parse(body);
        if (!equal(value.key, access)) {
          send(401, { error: "invalid_key" });
          return;
        }
      } catch {
        send(400, { error: "invalid_request" });
        return;
      }
      for (const [k, expiry] of sessions)
        if (expiry < Date.now()) sessions.delete(k);
      if (sessions.size >= 16) sessions.delete(sessions.keys().next().value);
      const cookie = randomBytes(32).toString("hex");
      sessions.set(cookie, Date.now() + 8 * 3600000);
      res.setHeader(
        "Set-Cookie",
        `ow_console=${cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800`,
      );
      send(200, { ok: true });
      return;
    }
    const cookie = (req.headers.cookie || "")
      .split(";")
      .map((x) => x.trim())
      .find((x) => x.startsWith("ow_console="))
      ?.slice(11);
    if (!cookie || !sessions.has(cookie) || sessions.get(cookie) < Date.now()) {
      send(401, { error: "login_required" });
      return;
    }
    if (req.url === "/logout" && req.method === "POST") {
      if (req.headers.origin !== own) {
        send(403, { error: "origin_required" });
        return;
      }
      sessions.delete(cookie);
      res.setHeader(
        "Set-Cookie",
        "ow_console=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
      );
      send(200, { ok: true });
      return;
    }
    if (req.method !== "GET") {
      send(405, { error: "read_only" });
      return;
    }
    if (req.url === "/config") {
      send(200, { grafana_url: grafana });
      return;
    }
    if (!req.url.startsWith("/api/") || !allowed(req.url.slice(4))) {
      send(404, { error: "route_unavailable" });
      return;
    }
    if (active >= 24) {
      send(429, { error: "connection_limit" });
      return;
    }
    active++;
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    let upstream;
    try {
      const path = req.url.slice(4),
        stream = path.split("?")[0].endsWith("/events");
      // Retry GET only, and only before any response body is forwarded.
      for (const base of apis) {
        try {
          const response = await fetch(base + path, {
            headers: {
              Authorization: `Bearer ${token}`,
              ...(req.headers["last-event-id"]
                ? { "Last-Event-ID": req.headers["last-event-id"] }
                : {}),
            },
            redirect: "error",
            signal: AbortSignal.any([
              abort.signal,
              AbortSignal.timeout(stream ? 60000 : 20000),
            ]),
          });
          if (response.status >= 500) {
            await response.body?.cancel();
            continue;
          }
          upstream = response;
          break;
        } catch {
          if (abort.signal.aborted) throw Error("closed");
        }
      }
      if (!upstream) {
        send(503, { error: "apis_unavailable" });
        return;
      }
      for (const name of [
        "content-type",
        "content-length",
        "content-disposition",
        "etag",
      ])
        if (upstream.headers.has(name))
          res.setHeader(name, upstream.headers.get(name));
      res.writeHead(upstream.status);
      await pipeline(Readable.fromWeb(upstream.body), res);
    } catch {
      if (!res.headersSent && !res.destroyed)
        send(503, { error: "upstream_unavailable" });
      else res.destroy();
    } finally {
      active--;
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  await new Promise((r, j) => {
    server.once("error", j);
    server.listen(port, "127.0.0.1", r);
  });
  actualPort = server.address().port;
  return {
    server,
    url: `http://127.0.0.1:${actualPort}`,
    close: async () => {
      server.closeAllConnections();
      await new Promise((r) => server.close(r));
    },
  };
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const app = await start(JSON.parse(await readFile(process.argv[2], "utf8")));
  console.log(`OpenWorker Console ${app.url}`);
  for (const signal of ["SIGINT", "SIGTERM"])
    process.once(signal, () => app.close().then(() => process.exit(0)));
}
