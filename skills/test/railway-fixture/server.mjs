// Local qualification fixture, not an application template or multi-user auth.
import { createServer } from "node:http";
import { mkdir, readFile, writeFile, rename } from "node:fs/promises";
import { dirname } from "node:path";
import { timingSafeEqual } from "node:crypto";
import { Harness } from "@openworkerai/harness-sdk";
const stateDir = process.env.OPENWORKER_STATE_DIR;
const record = "/data/app/session.json";
const harness = await Harness.start({ stateDir, sandbox: "off" });
await harness.setModelProvider({
  name: "Fixture",
  api: "responses",
  baseUrl: "http://127.0.0.1:8081/v1",
  credentials: { apiKeyEnv: "OPENAI_API_KEY", storage: "memory" },
});
const agent = harness.createAgent({
  name: "reader",
  instructions: "Answer from the conversation.",
  defaultModel: { provider: "Fixture", name: "fixture" },
  tools: [],
  planning: false,
  memory: false,
  scheduling: false,
});
await agent.ready();
let session;
try {
  session = await agent.resumeSession(
    JSON.parse(await readFile(record, "utf8")),
  );
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  session = await agent.newSession();
  await mkdir(dirname(record), { recursive: true });
  await writeFile(record + ".tmp", JSON.stringify(session.reference()));
  await rename(record + ".tmp", record);
}
let draining = false,
  busy = false;
const server = createServer(async (req, res) => {
  if (req.url === "/health") {
    res.writeHead(draining ? 503 : 200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ready: !draining }));
    return;
  }
  const actual = Buffer.from(req.headers.authorization ?? ""),
    expected = Buffer.from("Bearer " + process.env.APP_ACCESS_TOKEN);
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    res.writeHead(401);
    res.end();
    return;
  }
  if (draining || busy) {
    res.writeHead(503);
    res.end();
    return;
  }
  if (req.url !== "/turn" || req.method !== "POST") {
    res.writeHead(404);
    res.end();
    return;
  }
  let input = "";
  for await (const chunk of req) {
    input += chunk;
    if (Buffer.byteLength(input) > 4096) {
      res.writeHead(413);
      res.end();
      return;
    }
  }
  let message;
  try {
    message = JSON.parse(input).message;
    if (typeof message !== "string" || !message.trim()) throw Error("input");
  } catch {
    res.writeHead(400);
    res.end();
    return;
  }
  busy = true;
  let finished = false;
  res.writeHead(200, {
    "content-type": "application/x-ndjson",
    "cache-control": "no-store",
  });
  res.flushHeaders();
  res.once("close", () => {
    if (!finished) void session.cancel().catch(() => {});
  });
  try {
    const run = await session.send(message);
    for await (const item of run.events()) {
      if (item.event.type === "assistant_delta")
        res.write(
          JSON.stringify({ type: "delta", text: item.event.data.text }) + "\n",
        );
    }
    const result = await run.result();
    res.write(
      JSON.stringify({
        type: "result",
        status: result.status,
        text: result.finalOutput,
      }) + "\n",
    );
  } catch {
    res.write(JSON.stringify({ type: "error" }) + "\n");
  } finally {
    finished = true;
    busy = false;
    res.end();
  }
});
server.listen(Number(process.env.PORT), process.env.HOST ?? "0.0.0.0", () =>
  console.log("ready"),
);
for (const signal of ["SIGTERM", "SIGINT"])
  process.once(signal, () => {
    if (draining) return;
    draining = true;
    const deadline = setTimeout(() => process.exit(1), 10000);
    deadline.unref();
    void (async () => {
      const closed = new Promise((resolve) => server.close(resolve));
      if (busy) await session.cancel();
      await harness.shutdown();
      server.closeAllConnections();
      await closed;
      clearTimeout(deadline);
      console.log("shutdown complete");
      process.exit(0);
    })().catch(() => process.exit(1));
  });
