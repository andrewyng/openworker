// Replay explicitly selected, metadata-only qualification snapshots on loopback.
// This is a review fixture, not a replacement for authenticated application APIs.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
const [file, port = "59605"] = process.argv.slice(2);
if (!file)
  throw Error(
    "Usage: node examples/serve-session.mjs OPENSHELL_TRACE_JSON [PORT]",
  );
const data = JSON.parse(await readFile(file, "utf8"));
const traces = [
  {
    timeline: data.timeline,
    summary: data.summary,
    children: data.children?.children || [],
  },
  ...(data.childTraces || []),
];
const records = traces.map((t) => ({ ...t.timeline.run, ...t.summary.run }));
const assets = new Map([
  ["/", new URL("session.html", import.meta.url)],
  ...["tracing.js", "tracing.css", "observability-data.js"].map((f) => [
    "/src/" + f,
    new URL("../src/" + f, import.meta.url),
  ]),
]);
const types = { js: "text/javascript", css: "text/css", html: "text/html" };
createServer(async (req, res) => {
  try {
    const u = new URL(req.url, "http://loopback");
    if (req.method !== "GET") {
      res.writeHead(405);
      res.end();
      return;
    }
    res.setHeader("cache-control", "no-store");
    if (assets.has(u.pathname)) {
      res.setHeader(
        "content-type",
        types[u.pathname === "/" ? "html" : u.pathname.split(".").pop()],
      );
      res.end(await readFile(assets.get(u.pathname)));
      return;
    }
    let value;
    if (u.pathname === "/api/v1/observability/runs") {
      const q = (u.searchParams.get("q") || "").toLowerCase();
      const filtered = records.filter((r) =>
        `${r.name} ${r.session_id} ${r.turn_id}`.toLowerCase().includes(q),
      );
      const offset = Number(u.searchParams.get("after") || 0),
        limit = Number(u.searchParams.get("limit") || 25);
      if (
        !Number.isSafeInteger(offset) ||
        offset < 0 ||
        !Number.isInteger(limit) ||
        limit < 1 ||
        limit > 100
      )
        throw Error("Invalid page");
      value = {
        runs: filtered.slice(offset, offset + limit),
        next_cursor:
          offset + limit < filtered.length ? String(offset + limit) : null,
      };
    } else {
      const m =
        /^\/api\/v1\/sessions\/([^/]+)\/observability\/([^/]+)\/(timeline|summary|children)$/.exec(
          u.pathname,
        );
      const t =
        m &&
        traces.find(
          (t) =>
            t.timeline.run.session_id === m[1] &&
            t.timeline.run.turn_id === m[2],
        );
      if (!t) {
        res.writeHead(404);
        res.end();
        return;
      }
      if (m[3] === "timeline") {
        const after = Number(u.searchParams.get("after") || 0),
          through = Number(
            u.searchParams.get("through") || Number.MAX_SAFE_INTEGER,
          );
        const all = t.timeline.page.steps.filter(
          (s) => s.cursor > after && s.cursor <= through,
        );
        value = {
          ...t.timeline,
          page: {
            steps: all.slice(0, 100),
            next: all.length > 100 ? all[99].cursor : null,
            through: t.timeline.page.through,
          },
        };
      } else
        value =
          m[3] === "children" ? { children: t.children || [] } : t.summary;
    }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(value));
  } catch {
    res.writeHead(400);
    res.end("Invalid fixture request");
  }
}).listen(Number(port), "127.0.0.1", () =>
  console.log(`Session tracing: http://127.0.0.1:${port}/`),
);
