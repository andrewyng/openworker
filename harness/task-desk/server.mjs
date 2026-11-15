// A single-operator application boundary. All execution remains in OpenWorker.
import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { randomBytes, timingSafeEqual, createHash } from "node:crypto";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const id = "[A-Za-z0-9_-]{1,80}";
const eq = (a, b) =>
  typeof a === "string" &&
  Buffer.byteLength(a) === Buffer.byteLength(b) &&
  timingSafeEqual(Buffer.from(a), Buffer.from(b));
const textOf = (v) =>
  typeof v === "string"
    ? v
    : Array.isArray(v)
      ? v.map((x) => x.text || "").join("\n")
      : "";
export function allowed(method, path) {
  if (
    typeof path !== "string" ||
    !/^\/[A-Za-z0-9_/?=&-]*$/.test(path) ||
    path.startsWith("//")
  )
    return false;
  const u = new URL(path, "http://local");
  if (u.pathname + u.search !== path) return false;
  const p = u.pathname;
  if (method === "GET") {
    if (p === "/sessions")
      return [...u.searchParams].every(([k, v]) =>
        k === "limit"
          ? /^\d{1,3}$/.test(v) && +v > 0 && +v <= 100
          : k === "after" && new RegExp(`^${id}$`).test(v),
      );
    if (new RegExp(`^/sessions/${id}/events$`).test(p))
      return [...u.searchParams].every(
        ([k, v]) => k === "after" && /^\d{1,16}$/.test(v),
      );
    return (
      !u.search &&
      new RegExp(
        `^/sessions/${id}(/(turns/${id}|controls/${id}|artifacts(/${id}(/content)?)?))?$`,
      ).test(p)
    );
  }
  return (
    method === "POST" &&
    !u.search &&
    (p === "/sessions" ||
      new RegExp(`^/sessions/${id}/(turns|controls|result)$`).test(p))
  );
}
function apiOrigin(value) {
  const u = new URL(value);
  if (
    (u.protocol !== "https:" &&
      !(
        u.protocol === "http:" && ["127.0.0.1", "[::1]"].includes(u.hostname)
      )) ||
    u.username ||
    u.password ||
    u.pathname !== "/" ||
    u.search ||
    u.hash
  )
    throw Error(
      "Use an explicit HTTPS API origin (HTTP allowed only on loopback)",
    );
  return u.origin;
}
async function body(req, limit = 140000) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw Error("body_limit");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}
export async function start(config) {
  const apis = config.apis.map(apiOrigin);
  if (!apis.length || apis.length > 4) throw Error("Configure 1–4 APIs");
  const token = (await readFile(config.api_token_file, "utf8")).trim();
  const access = (await readFile(config.access_key_file, "utf8")).trim();
  if (
    ![token, access].every((v) => /^[A-Za-z0-9_.-]{32,256}$/.test(v)) ||
    eq(token, access)
  )
    throw Error("Distinct application and API keys required");
  for (const k of ["environment_id", "coworker"])
    if (!new RegExp(`^${id}$`).test(config[k])) throw Error("Invalid " + k);
  if (typeof config.workspace !== "string" || !config.workspace.startsWith("/"))
    throw Error("Configure workspace");
  const upstream = async (path, method = "GET", payload, signal) => {
    const headers = { Authorization: `Bearer ${token}` };
    if (payload !== undefined)
      headers["Content-Type"] = Buffer.isBuffer(payload)
        ? "application/octet-stream"
        : "application/json";
    let last;
    for (const base of apis) {
      try {
        const r = await fetch(base + path, {
          method,
          headers,
          redirect: "error",
          body:
            payload === undefined
              ? undefined
              : Buffer.isBuffer(payload)
                ? payload
                : JSON.stringify(payload),
          signal: signal || AbortSignal.timeout(20000),
        });
        if (method === "GET" && r.status >= 500) {
          await r.body?.cancel();
          last = Error("upstream_unavailable");
          continue;
        }
        return r;
      } catch (e) {
        last = e;
        if (method !== "GET" || signal?.aborted) break;
      }
    }
    throw last || Error("upstream_unavailable");
  };
  // Fail closed: this app must never run with an administrator or broad key.
  const who = await upstream("/auth/me");
  const principal = await who.json();
  if (
    !who.ok ||
    principal.role !== "operator" ||
    principal.scope?.kind !== "owned" ||
    !principal.environments?.includes(config.environment_id)
  )
    throw Error(
      "Task Desk requires an owned-scope operator with the configured environment grant",
    );
  const assets = new Map(
    await Promise.all(
      [
        ["/", "index.html", "text/html"],
        ["/app.js", "app.js", "text/javascript"],
        ["/style.css", "style.css", "text/css"],
      ].map(async ([p, f, type]) => [
        p,
        {
          type,
          bytes: await readFile(new URL("public/" + f, import.meta.url)),
        },
      ]),
    ),
  );
  const sessions = new Map();
  let attempts = [],
    active = 0,
    own;
  const server = http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    );
    const send = (status, value) => {
      if (res.headersSent) return res.destroy();
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(value));
    };
    if (
      req.headers.host !== new URL(own).host ||
      (req.headers.origin && req.headers.origin !== own) ||
      ["cross-site", "same-site"].includes(req.headers["sec-fetch-site"])
    )
      return send(403, { error: "origin_denied" });
    if (req.method === "GET" && assets.has(req.url)) {
      const a = assets.get(req.url);
      res.writeHead(200, { "Content-Type": a.type });
      return res.end(a.bytes);
    }
    if (
      req.method !== "GET" &&
      (req.headers.origin !== own ||
        req.headers["content-type"] !== "application/json")
    )
      return send(403, { error: "origin_required" });
    if (req.url === "/login" && req.method === "POST") {
      attempts = attempts.filter((t) => Date.now() - t < 60000);
      if (attempts.length >= 10) return send(429, { error: "try_later" });
      attempts.push(Date.now());
      try {
        if (!eq((await body(req, 1024)).key, access))
          return send(401, { error: "invalid_key" });
      } catch {
        return send(400, { error: "invalid_request" });
      }
      for (const [k, s] of sessions)
        if (s.expiry < Date.now()) {
          s.abort.abort();
          sessions.delete(k);
        }
      if (sessions.size >= 16) return send(429, { error: "session_limit" });
      const cookie = randomBytes(32).toString("hex");
      sessions.set(cookie, {
        expiry: Date.now() + 8 * 3600000,
        abort: new AbortController(),
      });
      res.setHeader(
        "Set-Cookie",
        `ow_taskdesk=${cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800${own.startsWith("https:") ? "; Secure" : ""}`,
      );
      return send(200, { ok: true });
    }
    const cookie = (req.headers.cookie || "")
      .split(";")
      .map((x) => x.trim())
      .find((x) => x.startsWith("ow_taskdesk="))
      ?.slice(12);
    const login = sessions.get(cookie);
    if (!login || login.expiry < Date.now()) {
      login?.abort.abort();
      sessions.delete(cookie);
      return send(401, { error: "login_required" });
    }
    if (req.url === "/logout" && req.method === "POST") {
      login.abort.abort();
      sessions.delete(cookie);
      res.setHeader(
        "Set-Cookie",
        "ow_taskdesk=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
      );
      return send(200, { ok: true });
    }
    if (req.url === "/config" && req.method === "GET")
      return send(200, {
        coworker: config.coworker,
        mode: config.mode || "live",
      });
    const path = req.url?.startsWith("/api/") ? req.url.slice(4) : "";
    if (!allowed(req.method, path))
      return send(404, { error: "route_unavailable" });
    if (active >= 24) return send(429, { error: "connection_limit" });
    active++;
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    const stream = path.split("?")[0].endsWith("/events");
    const signal = AbortSignal.any([
      abort.signal,
      login.abort.signal,
      AbortSignal.timeout(
        Math.min(login.expiry - Date.now(), stream ? 60000 : 25000),
      ),
    ]);
    try {
      let payload;
      if (req.method === "POST") payload = await body(req);
      if (path === "/sessions" && req.method === "POST") {
        if (
          Object.keys(payload).some(
            (k) => !["request_id", "name"].includes(k),
          ) ||
          !new RegExp(`^${id}$`).test(payload.request_id) ||
          typeof payload.name !== "string" ||
          !payload.name.trim() ||
          payload.name.length > 120
        )
          return send(400, { error: "invalid_request" });
        payload = {
          request_id: payload.request_id,
          parameters: {
            name: payload.name,
            coworker: config.coworker,
            workspace: config.workspace,
            environment_id: config.environment_id,
          },
        };
      }
      if (req.method === "POST" && path.endsWith("/result")) {
        // Explicit export of the completed assistant response, not arbitrary host files.
        // Deterministic IDs make an interrupted publication safely retryable.
        const base = path.slice(0, -7),
          s = await upstream(base, "GET", undefined, signal);
        if (!s.ok) return send(s.status, { error: "snapshot_unavailable" });
        const snapshot = await s.json();
        if (
          snapshot.turn?.status !== "completed" ||
          payload.turn_id !== snapshot.turn.id
        )
          return send(409, { error: "result_not_complete" });
        const message = [...snapshot.messages]
          .reverse()
          .find((m) => m.role === "assistant" && textOf(m.content));
        if (!message) return send(409, { error: "no_assistant_result" });
        const bytes = Buffer.from(textOf(message.content) + "\n");
        if (bytes.length > 1024 * 1024)
          return send(413, { error: "result_too_large" });
        const digest = createHash("sha256").update(bytes).digest("hex");
        const registration = await upstream(
          base + "/artifacts",
          "POST",
          {
            request_id: "result-" + snapshot.turn.id,
            name: "result.md",
            turn_id: snapshot.turn.id,
            content_type: "text/markdown",
            size: bytes.length,
            sha256: digest,
          },
          signal,
        );
        if (!registration.ok)
          return send(registration.status, {
            error: "artifact_registration_failed",
          });
        const artifact = await registration.json();
        if (artifact.status !== "ready") {
          const upload = await upstream(
            base + `/artifacts/${artifact.id}/content`,
            "PUT",
            bytes,
            signal,
          );
          if (!upload.ok)
            return send(upload.status, { error: "artifact_upload_incomplete" });
          await upload.body?.cancel();
        }
        return send(200, { id: artifact.id });
      }
      let target = path;
      if (stream && req.headers["last-event-id"]) {
        if (!/^\d{1,16}$/.test(req.headers["last-event-id"]))
          return send(400, { error: "invalid_cursor" });
        target = path.split("?")[0] + "?after=" + req.headers["last-event-id"];
      }
      const r = await upstream(target, req.method, payload, signal);
      const headers = {
        "Content-Type": r.headers.get("content-type") || "application/json",
      };
      if (path.endsWith("/content")) {
        headers["Content-Type"] = "application/octet-stream";
        headers["Content-Disposition"] = 'attachment; filename="result.md"';
      }
      if (stream) headers["X-Accel-Buffering"] = "no";
      res.writeHead(r.status, headers);
      if (r.body) await pipeline(Readable.fromWeb(r.body), res);
      else res.end();
    } catch {
      if (!res.destroyed)
        send(503, {
          error:
            req.method === "GET"
              ? "upstream_unavailable"
              : "outcome_unknown_retry_same_request",
        });
    } finally {
      active--;
    }
  });
  const host = config.private_network ? "0.0.0.0" : "127.0.0.1";
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(config.port ?? 54350, host, resolve);
  });
  own = config.origin || `http://127.0.0.1:${server.address().port}`;
  const u = new URL(own);
  if (
    u.origin !== own ||
    (u.protocol !== "https:" &&
      !(u.protocol === "http:" && u.hostname === "127.0.0.1"))
  ) {
    server.close();
    throw Error("App origin must use HTTPS or loopback HTTP");
  }
  return {
    server,
    url: own,
    close: async () => {
      for (const s of sessions.values()) s.abort.abort();
      server.closeAllConnections();
      await new Promise((r) => server.close(r));
    },
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const app = await start(JSON.parse(await readFile(process.argv[2], "utf8")));
  console.log("Task Desk: " + app.url);
  for (const s of ["SIGTERM", "SIGINT"])
    process.once(s, async () => {
      await app.close();
      process.exit(0);
    });
}
