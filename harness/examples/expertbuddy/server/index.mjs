import http from "node:http";
import { readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve, extname } from "node:path";
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { samples, guides } from "./catalog.mjs";
import { photoInput, decision, text, AppError } from "./domain.mjs";
import { Store } from "./store.mjs";
import { EdgeAgent } from "./agent.mjs";
import { LocalAgent } from "./local-agent.mjs";
import { InspectionEngine } from "./engine.mjs";
const publicRoot = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../public",
);
const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
};
const safeId = (v) => /^[a-zA-Z0-9_-]{1,100}$/.test(v);
const json = (res, status, data) => {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(JSON.stringify(data));
};
async function body(req) {
  let n = 0,
    parts = [];
  for await (const b of req) {
    n += b.length;
    if (n > 12 * 1024 * 1024)
      throw new AppError(413, "Photos must be under 8 MB.");
    parts.push(b);
  }
  try {
    return JSON.parse(Buffer.concat(parts).toString());
  } catch {
    throw new AppError(400, "The request could not be read.");
  }
}
function equal(a, b) {
  return (
    typeof a === "string" &&
    typeof b === "string" &&
    Buffer.byteLength(a) === Buffer.byteLength(b) &&
    timingSafeEqual(Buffer.from(a), Buffer.from(b))
  );
}
export async function start(config = {}) {
  const mode = config.mode ?? process.env.INSPECTIONBUDDY_MODE ?? "local",
    port = config.port ?? Number(process.env.PORT ?? 4336),
    host = config.host ?? process.env.HOST ?? "127.0.0.1";
  const frontend = mode === "frontend", local = mode === "local";
  if (local && !["127.0.0.1", "localhost", "::1"].includes(host))
    throw new Error("Local mode must listen on loopback. Use edge mode with authentication for remote access.");
  if (!["local", "frontend", "edge"].includes(mode))
    throw new Error("INSPECTIONBUDDY_MODE must be local, frontend or edge.");
  let origin =
    config.origin ?? process.env.PUBLIC_ORIGIN ?? `http://127.0.0.1:${port}`;
  const appToken =
    config.appToken ??
    (process.env.EDGE_APP_TOKEN_FILE
      ? (await readFile(process.env.EDGE_APP_TOKEN_FILE, "utf8")).trim()
      : null);
  const upstream =
    config.upstream ?? process.env.EDGE_API_URL ?? "http://127.0.0.1:18447";
  if (mode === "edge" && !appToken)
    throw new Error("The edge application needs an application token file.");
  if (frontend) {
    const u = new URL(upstream);
    if (
      !["http:", "https:"].includes(u.protocol) ||
      u.username ||
      u.password ||
      u.search ||
      u.hash ||
      u.pathname !== "/"
    )
      throw new Error("Use a fixed edge API origin without credentials.");
    if (
      u.protocol === "http:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)
    )
      throw new Error("Non-loopback edge connections require HTTPS.");
  }
  const data = config.dataDir ?? process.env.DATA_DIR ?? resolve(".data");
  let store, agent, engine;
  const sampleDigests = new Set();
  if (!frontend) {
    for (const sample of samples) {
      const bytes = await readFile(join(publicRoot, "samples", sample.file));
      sampleDigests.add(createHash("sha256").update(bytes).digest("hex"));
    }
    store = await new Store(data).init();
    agent =
      config.agentAdapter ??
      new (local ? LocalAgent : EdgeAgent)({
        dataDir: data,
        serviceUrl: process.env.OW_SERVICE_URL,
        namespace: process.env.OW_SERVICE_NAMESPACE ?? "local",
        tokenFile: process.env.OW_SERVICE_TOKEN_FILE,
        agent: process.env.OW_AGENT ?? (local ? "inspectionbuddy-v1" : "inspectionbuddy@1"),
        model: process.env.OW_MODEL ?? "gpt-6-luna",
      });
    engine = new InspectionEngine(store, agent);
    await engine.recover();
  }
  const connections = new Set();
  const server = http.createServer(async (req, res) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    );
    try {
      const url = new URL(req.url, "http://internal"),
        path = url.pathname;
      const write = !["GET", "HEAD"].includes(req.method);
      if (frontend || local) {
        const boundPort = server.address()?.port ?? port;
        const allowed = new Set([
          new URL(origin).host,
          `localhost:${boundPort}`,
          `127.0.0.1:${boundPort}`,
        ]);
        if (!allowed.has(req.headers.host))
          throw new AppError(403, "Unrecognized application host.");
        if (
          req.headers.origin &&
          req.headers.origin !== origin &&
          req.headers.origin !== `http://localhost:${port}`
        )
          throw new AppError(
            403,
            "This request did not originate in the application.",
          );
        if (
          write &&
          !req.headers["content-type"]?.startsWith("application/json")
        )
          throw new AppError(415, "Use JSON requests.");
      }
      if (path === "/healthz")
        return json(res, 200, {
          ok: true,
          role: frontend ? "inspection-ui" : "edge-application",
        });
      if (frontend && path.startsWith("/api/")) {
        const ctrl = new AbortController();
        req.on("aborted", () => ctrl.abort());
        res.on("close", () => ctrl.abort());
        const request = {
          method: req.method,
          signal: ctrl.signal,
          headers: {
            accept: req.headers.accept ?? "application/json",
            ...(appToken ? { authorization: `Bearer ${appToken}` } : {}),
          },
        };
        if (write) {
          request.body = JSON.stringify(await body(req));
          request.headers["content-type"] = "application/json";
        }
        let result;
        try {
          result = await fetch(new URL(path + url.search, upstream), request);
        } catch {
          if (ctrl.signal.aborted) return;
          return json(res, 503, {
            error:
              "The edge server is not connected. The sample library is available; assessments resume when the connection is restored.",
          });
        }
        res.writeHead(result.status, {
          "content-type":
            result.headers.get("content-type") ?? "application/json",
          "cache-control": "no-store",
          ...(result.headers.get("content-type")?.includes("text/event-stream")
            ? { "x-accel-buffering": "no" }
            : {}),
        });
        try {
          if (result.body)
            for await (const b of result.body) {
              if (res.destroyed) break;
              res.write(b);
            }
          res.end();
        } catch {
          res.destroy();
        }
        return;
      }
      if (!frontend && path.startsWith("/api/")) {
        if (!local && !equal(req.headers.authorization, `Bearer ${appToken}`))
          throw new AppError(
            401,
            "The edge application connection needs authentication.",
          );
        if (path === "/api/status" && req.method === "GET")
          return json(res, 200, {
            role: local ? "local-development" : "edge-server",
            agent: await agent.health(),
            photos: local ? "local-directory" : "edge-volume",
            history: local ? "local-directory" : "edge-volume",
            inference: "OpenAI cloud vision",
            sampleOnly: false,
          });
        if (path === "/api/samples" && req.method === "GET")
          return json(res, 200, { samples });
        if (path === "/api/guides" && req.method === "GET")
          return json(res, 200, { guides });
        if (path === "/api/inspections" && req.method === "GET") {
          const q = text(url.searchParams.get("q"), 200).toLowerCase();
          return json(res, 200, {
            inspections: store
              .all()
              .filter(
                (r) =>
                  !q ||
                  [
                    r.code,
                    r.part,
                    r.station,
                    r.report?.title,
                    r.decision?.inspector,
                  ]
                    .join(" ")
                    .toLowerCase()
                    .includes(q),
              )
              .slice(0, 100)
              .map(({ photoFile, requestHash, ...r }) => r),
          });
        }
        if (path === "/api/inspections" && req.method === "POST") {
          const input = await body(req);
          if (!safeId(input.requestId ?? ""))
            throw new AppError(400, "A request ID is required.");
          const existing = store
            .all()
            .find((r) => r.requestId === input.requestId);
          let photo, sample;
          if (input.sampleId) {
            sample = samples.find((s) => s.id === input.sampleId);
            if (!sample) throw new AppError(400, "Unknown sample.");
            photo = {
              bytes: await readFile(join(publicRoot, "samples", sample.file)),
              mediaType: "image/png",
              name: sample.file,
            };
          } else photo = photoInput(input.photo);
          const part = text(input.part ?? sample?.part, 100),
            station = text(input.station ?? sample?.station, 100);
          if (!part || !station)
            throw new AppError(400, "Choose a part and station.");
          const requestHash = createHash("sha256")
            .update(photo.bytes)
            .update(JSON.stringify({ part, station, notes: text(input.notes) }))
            .digest("hex");
          if (existing) {
            if (existing.requestHash !== requestHash)
              throw new AppError(
                409,
                "This request ID already belongs to another photo.",
              );
            return json(res, 200, existing);
          }
          if (engine.active.size >= 3)
            throw new AppError(
              429,
              "Three inspections are already running. Please wait a moment.",
            );
          const health = await agent.health();
          if (!health.connected)
            throw new AppError(
              503,
              local ? "The local harness is not ready. Check your .env configuration; your photo has not been submitted." : "The agent service is not connected. Your photo has not been submitted.",
            );
          const id = randomUUID().replaceAll("-", ""),
            photoFile = `${id}${photo.mediaType === "image/jpeg" ? ".jpg" : photo.mediaType === "image/webp" ? ".webp" : ".png"}`;
          await writeFile(join(data, "photos", photoFile), photo.bytes, {
            mode: 0o600,
            flag: "wx",
          });
          const now = new Date().toISOString();
          const record = await store.insert({
            id,
            code: `EB-${String(store.all().length + 1).padStart(4, "0")}`,
            requestId: input.requestId,
            requestHash,
            part,
            station,
            notes: text(input.notes),
            synthetic:
              !!sample ||
              sampleDigests.has(
                createHash("sha256").update(photo.bytes).digest("hex"),
              ),
            sampleId: sample?.id ?? null,
            photoFile,
            photoName: photo.name,
            mediaType: photo.mediaType,
            status: "analyzing",
            createdAt: now,
            updatedAt: now,
            revision: 1,
            cursor: 0,
            activity: [
              {
                label: "Photo saved on the edge server",
                at: now,
                type: "photo_saved",
              },
            ],
            conversation: [],
            report: null,
            decision: null,
            service: null,
            trace: null,
          });
          void engine.assess(record.id);
          return json(res, 201, record);
        }
        const m = path.match(
          /^\/api\/inspections\/([a-zA-Z0-9_-]+)(?:\/(events|photo|decision|followups))?$/,
        );
        if (m) {
          const id = m[1],
            action = m[2],
            record = store.get(id);
          if (!action && req.method === "GET") return json(res, 200, record);
          if (action === "photo" && req.method === "GET") {
            res.writeHead(200, {
              "content-type": record.mediaType,
              "cache-control": "private, max-age=3600",
            });
            return res.end(
              await readFile(join(data, "photos", record.photoFile)),
            );
          }
          if (action === "events" && req.method === "GET") {
            res.writeHead(200, {
              "content-type": "text/event-stream",
              "cache-control": "no-cache",
              connection: "keep-alive",
              "x-accel-buffering": "no",
            });
            const send = (r) => {
              if (!res.destroyed)
                res.write(`id: ${r.revision}\ndata: ${JSON.stringify(r)}\n\n`);
            };
            send(record);
            const unsub = engine.subscribe(id, send);
            const heartbeat = setInterval(() => {
              if (!res.destroyed) res.write(": heartbeat\n\n");
            }, 15000);
            connections.add(res);
            res.on("close", () => {
              unsub();
              clearInterval(heartbeat);
              connections.delete(res);
            });
            return;
          }
          if (action === "decision" && req.method === "POST") {
            const input = await body(req),
              d = decision(input);
            const saved = await engine.update(id, (r) => {
              if (!r.report || r.status === "analyzing")
                throw new AppError(
                  409,
                  "Wait for the assessment before recording a decision.",
                );
              if (input.revision !== r.revision)
                throw new AppError(
                  409,
                  "This inspection changed. Review its latest state before saving.",
                );
              r.decisionHistory = [
                ...(r.decisionHistory ?? []),
                ...(r.decision ? [r.decision] : []),
              ];
              r.decision = { ...d, at: new Date().toISOString() };
              r.status = "reviewed";
            });
            return json(res, 200, saved);
          }
          if (action === "followups" && req.method === "POST") {
            const input = await body(req);
            const question = text(input.question, 2000);
            if (!question || !safeId(input.requestId ?? ""))
              throw new AppError(400, "Enter a question.");
            if (record.followupRequestIds?.includes(input.requestId))
              return json(res, 200, record);
            await store.update(id, (r) => {
              if (r.followupBusy || engine.active.has(id) || !r.report)
                throw new AppError(
                  409,
                  "Wait for the current assessment or question to finish.",
                );
              r.followupBusy = true;
              r.followupRequestIds = [
                ...(r.followupRequestIds ?? []),
                input.requestId,
              ];
            });
            void engine
              .followup(id, question, `eb-followup-${input.requestId}`)
              .catch((e) =>
                console.error("Follow-up stopped", {
                  id,
                  code: e.code ?? e.name,
                }),
              );
            return json(res, 202, { accepted: true });
          }
        }
        throw new AppError(404, "Not found.");
      }
      if (req.method === "GET" || req.method === "HEAD") {
        let relative =
          path === "/" ? "index.html" : decodeURIComponent(path.slice(1));
        if (
          relative.includes("..") ||
          relative.includes("\\") ||
          relative.includes("\0")
        )
          throw new AppError(404, "Not found.");
        if (relative === "catalog.json")
          return json(res, 200, { samples, guides });
        if (
          !/^(index\.html|app\.js|usage\.js|style\.css|icons\.svg|samples\/[a-z-]+\.png)$/.test(
            relative,
          )
        )
          throw new AppError(404, "Not found.");
        let content;
        try {
          content = await readFile(join(publicRoot, relative));
        } catch {
          throw new AppError(404, "Not found.");
        }
        res.writeHead(200, {
          "content-type":
            types[extname(relative)] ?? "application/octet-stream",
          "cache-control": relative.startsWith("samples/")
            ? "public, max-age=3600"
            : "no-cache",
        });
        return res.end(req.method === "HEAD" ? undefined : content);
      }
      throw new AppError(404, "Not found.");
    } catch (e) {
      if (!res.headersSent)
        json(res, e.status ?? 500, {
          error:
            e instanceof AppError
              ? e.message
              : "The request could not be completed.",
        });
      else res.end();
      if (!(e instanceof AppError))
        console.error("Application request failed", { code: e.code ?? e.name });
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 15000;
  await new Promise((ok, no) => {
    server.once("error", no);
    server.listen(port, host, ok);
  });
  if (port === 0 && !config.origin && !process.env.PUBLIC_ORIGIN)
    origin = `http://127.0.0.1:${server.address().port}`;
  return {
    server,
    store,
    engine,
    url: `http://${host}:${server.address().port}`,
    close: async () => {
      for (const r of connections) r.end();
      await agent?.close?.();
      server.closeAllConnections();
      await new Promise((ok) => server.close(ok));
    },
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const app = await start();
  console.log(
    `InspectionBuddy ${process.env.INSPECTIONBUDDY_MODE ?? "local"}: ${app.url}`,
  );
  for (const signal of ["SIGINT", "SIGTERM"])
    process.once(signal, async () => {
      await app.close();
      process.exit(0);
    });
}
