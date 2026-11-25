import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { Service } from "@openworkerai/harness-sdk";

// In a hosted app this catalog comes from the signed-in user's project store.
// Project IDs stay stable across process restarts. Workspace IDs needn't.
const projects = [
  { id: "garden", title: "Alice’s garden", owner: "alice" },
  { id: "studio", title: "Bob’s studio", owner: "bob" },
];
const credentials = new Map();
for (const user of ["alice", "bob"]) {
  const token = process.env[`WEBSITE_${user.toUpperCase()}_TOKEN`];
  if (!token || token.length < 32 || credentials.has(token)) {
    throw new Error("Provide a distinct demo access token of at least 32 characters for each user.");
  }
  credentials.set(token, user);
}

const service = await Service.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: process.env.OW_SERVICE_NAMESPACE,
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
const provider = await service.workspaces.startProvider({ name: "websites", options: { podProfile: "small" } });
const active = new Set();

async function closeSession(session) {
  const idempotencyKey = randomUUID();
  for (let i = 0; ; i++) {
    try { return await session.close({ idempotencyKey }); }
    catch (err) {
      // The worker can still hold the turn briefly after the final event.
      if (err.status !== 409 || i >= 30) throw err;
      await delay(150);
    }
  }
}

async function runProject(project, mode, brief) {
  const key = `website-builder/${project.id}`;
  const builder = await provider.getOrCreate({ key: key + "/builder" });
  const workspace = mode === "review-separate"
    ? await provider.getOrCreate({
        key: key + "/reviewer", dataId: builder.dataId, options: { podProfile: "medium" },
      })
    : builder;
  const agent = await service.agent(mode === "build" ? "builder" : "reviewer");
  const session = await agent.newSession({ workspaceId: workspace.workspaceId });
  try {
    const run = await session.send(brief);
    const result = await run.result();
    const calls = new Map();
    for await (const { event } of run.events()) {
      if (event.type === "tool_started") {
        calls.set(event.data.call_id, { name: event.data.name, status: "started" });
      } else if (event.type === "tool_finished") {
        const { name, result: output } = event.data;
        calls.set(event.data.call_id, { name, status: output.isError ? "failed" : "finished", output: output.structuredContent });
      }
    }
    return { sessionId: session.id, status: result.status, message: result.finalOutput, tools: [...calls.values()] };
  } finally {
    await closeSession(session);
  }
}

const page = await readFile(new URL("./index.html", import.meta.url));
function reply(res, status, data) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
  res.end(JSON.stringify(data));
}
const server = createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8", "referrer-policy": "no-referrer" });
      return res.end(page);
    }
    const token = /^Bearer (\S+)$/.exec(req.headers.authorization ?? "")?.[1];
    const owner = credentials.get(token);
    if (!owner) return reply(res, 401, { error: "Enter your project access token." });
    if (req.method === "GET" && req.url === "/projects") {
      return reply(res, 200, projects.filter(p => p.owner === owner).map(({ id, title }) => ({ id, title })));
    }
    const id = /^\/projects\/([a-z-]+)\/runs$/.exec(req.url)?.[1];
    const project = projects.find(p => p.id === id && p.owner === owner);
    if (req.method !== "POST" || !project) return reply(res, 404, { error: "Project not found." });
    let bytes = 0;
    const chunks = [];
    for await (const chunk of req) {
      bytes += chunk.length;
      if (bytes > 32_768) return reply(res, 413, { error: "Brief is too long." });
      chunks.push(chunk);
    }
    let body;
    try { body = JSON.parse(Buffer.concat(chunks)); } catch { /* validated below */ }
    if (!body || Object.keys(body).some(k => !["mode", "brief"].includes(k)) ||
        !["build", "review-shared", "review-separate"].includes(body.mode) ||
        typeof body.brief !== "string" || !body.brief.trim()) {
      return reply(res, 400, { error: "Supply a mode and a nonempty brief." });
    }
    if (active.has(id)) return reply(res, 409, { error: "A run is already using this project." });
    active.add(id);
    try { reply(res, 200, await runProject(project, body.mode, body.brief)); }
    finally { active.delete(id); }
  } catch (err) {
    console.error("Project run failed", err.code ?? err.name);
    reply(res, 502, { error: "The workspace or agent could not complete the request." });
  }
});
server.listen(Number(process.env.PORT ?? 4175), "127.0.0.1", () => {
  console.log(`Website builder listening at http://127.0.0.1:${server.address().port}`);
});
process.once("SIGTERM", () => {
  server.close(() => service.disconnect());
  server.closeIdleConnections();
});
