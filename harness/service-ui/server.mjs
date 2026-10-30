// Browser boundary only. The service remains the authority for every action.
import http from "node:http";
import { readFile } from "node:fs/promises";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
const id = "[A-Za-z0-9_@.-]{1,100}";
const tokenPattern = /^[A-Za-z0-9_.-]{32,256}$/;
const eq = (a, b) =>
  typeof a === "string" &&
  typeof b === "string" &&
  Buffer.byteLength(a) === Buffer.byteLength(b) &&
  timingSafeEqual(Buffer.from(a), Buffer.from(b));
function endpoint(raw, allowInternalHttp = false) {
  const u = new URL(raw);
  if (
    u.username ||
    u.password ||
    u.search ||
    u.hash ||
    u.pathname !== "/" ||
    (u.protocol !== "https:" &&
      !(
        u.protocol === "http:" &&
        (allowInternalHttp ||
          ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname))
      ))
  )
    throw Error("Use HTTPS or explicit local/internal HTTP origin");
  return u.origin;
}
export function allowed(method, path) {
  // Search text is URL encoded; path IDs remain strictly unencoded. This is
  // deliberately not a generic proxy or an arbitrary query passthrough.
  if (method === "GET" && allowedRecording(path)) return true;
  if (!/^\/v1\/[A-Za-z0-9_@./?=&-]+$/.test(path) || path.includes(".."))
    return false;
  const u = new URL(path, "http://local");
  if (u.pathname + u.search !== path) return false;
  const p = u.pathname;
  if (method === "GET") {
    const list = ["/v1/sessions", "/v1/credentials", "/v1/audit"];
    const events = new RegExp(`^/v1/sessions/${id}/events(/page)?$`).test(p);
    if (u.search && !list.includes(p) && !events) return false;
    const seen = new Set();
    for (const [k, v] of u.searchParams) {
      if (seen.has(k)) return false;
      seen.add(k);
      if (
        k === "limit" &&
        p === "/v1/sessions" &&
        /^(?:[1-9][0-9]?|100)$/.test(v)
      )
        continue;
      if (
        k === "after" &&
        (events || p === "/v1/audit"
          ? /^\d{1,15}$/.test(v)
          : (p === "/v1/sessions" || p === "/v1/credentials") &&
            new RegExp(`^${id}$`).test(v))
      )
        continue;
      return false;
    }
    return (
      /^\/v1\/(capabilities|auth\/me|sessions|profiles|releases|configuration|worker-pools|credentials|audit|environments)$/.test(
        p,
      ) ||
      new RegExp(
        `^/v1/(profiles|releases|operations|environments)/${id}$`,
      ).test(p) ||
      new RegExp(
        `^/v1/sessions/${id}(/events(/page)?|/(runs|turns|controls)/${id})?$`,
      ).test(p)
    );
  }
  if (u.search) return false;
  if (method === "PUT") return p === "/v1/configuration";
  return (
    method === "POST" &&
    (/^\/v1\/(releases|profiles|submissions|sessions|credentials)$/.test(p) ||
      new RegExp(`^/v1/(releases|profiles)/${id}/activate$`).test(p) ||
      new RegExp(`^/v1/worker-pools/${id}/scale$`).test(p) ||
      new RegExp(`^/v1/credentials/${id}/revoke$`).test(p) ||
      new RegExp(`^/v1/environments/${id}/delete$`).test(p) ||
      new RegExp(`^/v1/sessions/${id}/(turns|controls|close)$`).test(p))
  );
}
function allowedRecording(path) {
  if (path.length > 4000 || path.includes("#")) return false;
  const u = new URL(path, "http://local");
  if (u.pathname + u.search !== path) return false;
  const listing = /^\/v1\/observability\/(runs|summary)$/.test(u.pathname);
  const timeline = new RegExp(
    `^/v1/sessions/${id}/observability/${id}/timeline$`,
  ).test(u.pathname);
  const summary = new RegExp(
    `^/v1/sessions/${id}/observability/${id}/summary$`,
  ).test(u.pathname);
  const health = u.pathname === "/v1/observability/health";
  if (!listing && !timeline && !summary && !health) return false;
  try {
    decodeURIComponent(u.search.replaceAll("+", " "));
  } catch {
    return false;
  }
  const seen = new Set();
  for (const [key, value] of u.searchParams) {
    if (seen.has(key)) return false;
    seen.add(key);
    if (listing) {
      if (
        key === "q" &&
        Buffer.byteLength(value) <= 200 &&
        !/[\x00-\x1f\x7f-\x9f]/.test(value)
      )
        continue;
      if (
        key === "agent" &&
        value.length > 0 &&
        Buffer.byteLength(value) <= 128 &&
        !/[\x00-\x1f\x7f-\x9f]/.test(value)
      )
        continue;
      if (
        ["from_ms", "to_ms"].includes(key) &&
        /^\d{1,15}$/.test(value) &&
        BigInt(value) <= 253402300799000n
      )
        continue;
      if (key === "after" && /^[A-Za-z0-9_-]{1,1024}$/.test(value)) continue;
    }
    if (
      key === "limit" &&
      (listing || timeline) &&
      /^[1-9]\d{0,2}$/.test(value) &&
      Number(value) <=
        (timeline ? 200 : u.pathname.endsWith("/summary") ? 25 : 100)
    )
      continue;
    if (
      ((timeline && key === "after") ||
        ((timeline || summary) && key === "through")) &&
      /^\d{1,19}$/.test(value) &&
      BigInt(value) <= 9223372036854775807n
    )
      continue;
    return false;
  }
  return true;
}
async function body(req) {
  if (req.headers["content-type"]?.split(";")[0] !== "application/json")
    throw Object.assign(Error("json_required"), { status: 415 });
  let size = 0;
  const parts = [];
  for await (const part of req) {
    size += part.length;
    if (size > 160000)
      throw Object.assign(Error("request_too_large"), { status: 413 });
    parts.push(part);
  }
  try {
    return JSON.parse(Buffer.concat(parts).toString("utf8"));
  } catch {
    throw Object.assign(Error("invalid_json"), { status: 400 });
  }
}
async function jsonResponse(response) {
  let size = 0;
  const parts = [];
  for await (const part of response.body) {
    size += part.length;
    if (size > 4 * 1024 * 1024) throw Error("response_too_large");
    parts.push(Buffer.from(part));
  }
  return JSON.parse(Buffer.concat(parts).toString("utf8"));
}
export async function start(config) {
  const upstream = endpoint(
    config.serviceUrl,
    config.allowInternalHttp === true,
  );
  if (
    !/^[A-Za-z0-9_@.-]{1,80}$/.test(config.namespace) ||
    !/^[A-Za-z0-9_@.-]{1,80}$/.test(config.serviceId)
  )
    throw Error("Explicit namespace and serviceId required");
  let origin = config.publicOrigin ? endpoint(config.publicOrigin) : null;
  const host = config.host || "127.0.0.1";
  if (!["127.0.0.1", "::1"].includes(host) && !origin)
    throw Error("publicOrigin required for non-loopback binding");
  if (
    origin &&
    !origin.startsWith("https:") &&
    !["127.0.0.1", "localhost", "[::1]"].includes(new URL(origin).hostname)
  )
    throw Error("Browser public origin must use HTTPS");
  const ttl = Math.min(config.sessionSeconds || 1800, 3600) * 1000;
  if (!Number.isFinite(ttl) || !Number.isInteger(ttl) || ttl < 1000)
    throw Error("Invalid session lifetime");
  const sessions = new Map();
  let loginInFlight = 0;
  let loginBudget = 60;
  let budgetAt = Date.now();
  function remove(key) {
    const s = sessions.get(key);
    if (s) {
      for (const c of s.requests) c.abort();
      s.token = "";
      sessions.delete(key);
    }
  }
  const cleanup = setInterval(() => {
    for (const [key, s] of sessions) if (s.expires <= Date.now()) remove(key);
  }, 1000);
  cleanup.unref();
  function headers(token) {
    return {
      Authorization: "Bearer " + token,
      "x-openworker-namespace": config.namespace,
      "x-openworker-service-id": config.serviceId,
    };
  }
  async function call(path, token, options = {}) {
    const r = await fetch(upstream + path, {
      ...options,
      headers: { ...headers(token), ...options.headers },
      redirect: "error",
      signal: options.signal || AbortSignal.timeout(10000),
    });
    if (
      r.headers.get("x-openworker-service-id") !== config.serviceId ||
      r.headers.get("x-openworker-namespace") !== config.namespace
    )
      throw Error("service_identity_mismatch");
    return r;
  }
  const server = http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    );
    const send = (code, value) => {
      res
        .writeHead(code, { "Content-Type": "application/json" })
        .end(JSON.stringify(value));
    };
    try {
      if (req.headers.host !== new URL(origin).host) {
        send(403, { error: "invalid_host" });
        return;
      }
      if (req.headers.origin && req.headers.origin !== origin) {
        send(403, { error: "invalid_origin" });
        return;
      }
      if (
        req.headers["sec-fetch-site"] &&
        !["same-origin", "none"].includes(req.headers["sec-fetch-site"])
      ) {
        send(403, { error: "cross_site_request" });
        return;
      }
      const method = req.method,
        path = req.url;
      if (!["GET", "HEAD", "POST", "PUT"].includes(method)) {
        send(405, { error: "method_not_allowed" });
        return;
      }
      if (!["GET", "HEAD"].includes(method) && req.headers.origin !== origin) {
        send(403, { error: "origin_required" });
        return;
      }
      const cookieName = origin.startsWith("https:") ? "__Host-ow_ui" : "ow_ui";
      const sessionKey = (req.headers.cookie || "")
        .split(";")
        .map((x) => x.trim())
        .find((x) => x.startsWith(cookieName + "="))
        ?.slice(cookieName.length + 1);
      let s = sessions.get(sessionKey);
      if (s && s.expires <= Date.now()) {
        remove(sessionKey);
        s = null;
      }
      const cookie = (value, seconds) =>
        `${cookieName}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${seconds}${origin.startsWith("https:") ? "; Secure" : ""}`;
      if (path === "/login" && method === "POST") {
        if (Date.now() - budgetAt > 60000) {
          loginBudget = 60;
          budgetAt = Date.now();
        }
        if (loginInFlight >= 8 || --loginBudget < 0 || sessions.size >= 128) {
          send(429, { error: "login_limit" });
          return;
        }
        const input = await body(req);
        if (
          typeof input?.token !== "string" ||
          !tokenPattern.test(input.token)
        ) {
          send(400, { error: "invalid_token" });
          return;
        }
        loginInFlight++;
        try {
          const r = await call("/v1/auth/me", input.token);
          if (!r.ok) {
            send(r.status, { error: "credential_rejected" });
            return;
          }
          const identity = await jsonResponse(r);
          const cap = await call("/v1/capabilities", input.token);
          if (!cap.ok) throw Error("capabilities_unavailable");
          const capabilities = await jsonResponse(cap);
          if (
            capabilities.apiVersion !== "1" ||
            capabilities.serviceId !== config.serviceId ||
            capabilities.namespace !== config.namespace
          )
            throw Error("service_identity_mismatch");
          if (sessionKey) remove(sessionKey);
          const key = randomBytes(32).toString("hex"),
            csrf = randomBytes(32).toString("hex");
          const expires = Math.min(
            Date.now() + ttl,
            identity.expires_at ? identity.expires_at * 1000 : Infinity,
          );
          if (expires <= Date.now()) {
            send(401, { error: "credential_expired" });
            return;
          }
          sessions.set(key, {
            token: input.token,
            csrf,
            identity,
            capabilities,
            expires,
            requests: new Set(),
          });
          input.token = "";
          res.setHeader(
            "Set-Cookie",
            cookie(key, Math.ceil((expires - Date.now()) / 1000)),
          );
          send(200, { identity, capabilities, csrf, expires });
        } finally {
          loginInFlight--;
        }
        return;
      }
      if (path === "/session" && method === "GET") {
        if (!s) {
          send(401, { error: "login_required" });
          return;
        }
        const r = await call("/v1/auth/me", s.token);
        if (!r.ok) {
          remove(sessionKey);
          send(r.status, { error: "credential_rejected" });
          return;
        }
        s.identity = await jsonResponse(r);
        send(200, {
          identity: s.identity,
          capabilities: s.capabilities,
          csrf: s.csrf,
          expires: s.expires,
        });
        return;
      }
      if (path === "/logout" && method === "POST") {
        if (!s || !eq(req.headers["x-csrf-token"], s.csrf)) {
          send(403, { error: "csrf_required" });
          return;
        }
        remove(sessionKey);
        res.setHeader("Set-Cookie", cookie("", 0));
        send(200, { ok: true });
        return;
      }
      if (path.startsWith("/api/")) {
        if (!s) {
          send(401, { error: "login_required" });
          return;
        }
        const target = path.slice(4);
        if (!allowed(method, target)) {
          send(404, { error: "unsupported_route" });
          return;
        }
        const write = method !== "GET";
        if (write && !eq(req.headers["x-csrf-token"], s.csrf)) {
          send(403, { error: "csrf_required" });
          return;
        }
        const key = req.headers["idempotency-key"];
        if (
          write &&
          (typeof key !== "string" || !/^[A-Za-z0-9_-]{1,100}$/.test(key))
        ) {
          send(400, { error: "idempotency_key_required" });
          return;
        }
        if (s.requests.size >= 8) {
          send(429, { error: "request_limit" });
          return;
        }
        const data = write ? await body(req) : undefined;
        const controller = new AbortController();
        s.requests.add(controller);
        const timer = setTimeout(
          () => controller.abort(),
          target.match(/\/events\?/) || target.endsWith("/events")
            ? 60000
            : 15000,
        );
        res.on("close", () => controller.abort());
        try {
          // Never automatically replay mutations after a lost response. The UI retains
          // the same idempotency key/body for deliberate reconciliation or retry.
          // Unknown tool outcomes are returned to the model by the harness, not retried here.
          const r = await call(target, s.token, {
            method,
            signal: controller.signal,
            headers: write
              ? { "Content-Type": "application/json", "Idempotency-Key": key }
              : {},
            body: write ? JSON.stringify(data) : undefined,
          });
          if (r.status === 401) {
            remove(sessionKey);
            send(401, { error: "credential_expired_or_revoked" });
            return;
          }
          if (
            r.headers.get("content-type")?.includes("text/event-stream") &&
            r.ok
          ) {
            res.writeHead(200, {
              "Content-Type": "text/event-stream",
              "X-Accel-Buffering": "no",
            });
            res.flushHeaders();
            await pipeline(Readable.fromWeb(r.body), res);
          } else {
            let value;
            try {
              value = await jsonResponse(r);
            } catch (error) {
              if (r.ok) throw error;
              // Axum validation errors may be plain text. Preserve a definitive
              // rejection instead of trapping the UI in an unknown-write retry.
              value = { error: "request_rejected" };
            }
            send(r.status, value);
          }
        } catch {
          if (!res.headersSent && !res.destroyed)
            send(502, {
              error: write ? "outcome_unknown" : "service_unavailable",
              idempotencyKey: write ? key : undefined,
            });
        } finally {
          clearTimeout(timer);
          s.requests.delete(controller);
        }
        return;
      }
      const assets = {
        "/": ["index.html", "text/html"],
        "/index.html": ["index.html", "text/html"],
        "/app.js": ["app.js", "text/javascript"],
        "/observability.js": ["observability.js", "text/javascript"],
        "/observability-data.js": ["observability-data.js", "text/javascript"],
        "/style.css": ["style.css", "text/css"],
      };
      if (["GET", "HEAD"].includes(method) && assets[path]) {
        const [file, type] = assets[path];
        const bytes = await readFile(
          new URL("./public/" + file, import.meta.url),
        );
        res
          .writeHead(200, { "Content-Type": type + "; charset=utf-8" })
          .end(method === "HEAD" ? undefined : bytes);
        return;
      }
      send(404, { error: "not_found" });
    } catch (e) {
      if (!res.headersSent && !res.destroyed)
        send(e.status || 502, {
          error: e.status ? e.message : "service_unavailable",
        });
    }
  });
  server.requestTimeout = 20000;
  server.headersTimeout = 10000;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(config.port ?? 58641, host, resolve);
  });
  origin ||= "http://127.0.0.1:" + server.address().port;
  return {
    server,
    url: origin,
    close: async () => {
      clearInterval(cleanup);
      for (const k of sessions.keys()) remove(k);
      server.closeAllConnections();
      await new Promise((r) => server.close(r));
    },
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw Error("Usage: node server.mjs config.json");
  const app = await start(JSON.parse(await readFile(process.argv[2], "utf8")));
  console.log("OpenWorker management UI: " + app.url);
  for (const signal of ["SIGINT", "SIGTERM"])
    process.once(signal, async () => {
      await app.close();
      process.exit(0);
    });
}
