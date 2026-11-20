// External observer for workspace_preview_ports. Only loopback listeners and a
// disposable browser context. The test app authorizes its own host-scoped cookie.
import { createRequire } from "node:module";
import { readFile, writeFile, access } from "node:fs/promises";
import https from "node:https";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.OW_TEST_PLAYWRIGHT || "playwright");
const dir = process.env.OW_PREVIEW_BARRIER;
async function checkpoint(id) {
  const marker = join(dir, id + ".json");
  for (let i = 0; i < 1200; i++) {
    try {
      await access(marker);
      return JSON.parse(await readFile(marker, "utf8"));
    } catch (e) {
      if (e.code !== "ENOENT") throw e;
    }
    await delay(100);
  }
  throw Error("Timed out waiting for " + id);
}
const { slots } = await checkpoint("loaded");
const session = randomUUID(),
  routes = new Map(slots.map((s) => [new URL(s.url).hostname, s]));
const ca = await readFile(process.env.OW_PREVIEW_CA);
const gateway = new URL(process.env.OW_PREVIEW_GATEWAY);
assert.equal(gateway.hostname, "127.0.0.1");
function forward(req) {
  const host = req.headers.host?.split(":")[0];
  const origin = "https://" + req.headers.host;
  if (
    !routes.has(host) ||
    req.headers.cookie !== "__Host-demo=" + session ||
    (req.headers.origin && req.headers.origin !== origin)
  )
    return null;
  const headers = {
    ...req.headers,
    host,
    authorization: "Bearer " + routes.get(host).accessToken,
  };
  delete headers.cookie;
  if (headers.origin) headers.origin = "https://" + host;
  return https.request({
    hostname: gateway.hostname,
    port: gateway.port,
    ca,
    method: req.method,
    path: req.url,
    headers,
  });
}
const front = https.createServer(
  {
    key: await readFile(process.env.OW_TEST_TLS_KEY),
    cert: await readFile(process.env.OW_TEST_TLS_CERT),
  },
  (req, res) => {
    const up = forward(req);
    if (!up) {
      res.writeHead(403, { "content-type": "text/plain" }).end("Forbidden");
      return;
    }
    up.on("response", (r) => {
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    });
    up.on("error", () => res.destroy());
    req.pipe(up);
  },
);
front.on("upgrade", (req, socket, head) => {
  const up = forward(req);
  if (!up) {
    socket.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    return;
  }
  up.on("upgrade", (r, peer, tail) => {
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\n" +
        Object.entries(r.headers)
          .map(([k, v]) => k + ": " + v + "\r\n")
          .join("") +
        "\r\n",
    );
    if (head.length) peer.write(head);
    if (tail.length) socket.write(tail);
    socket.on("error", () => peer.destroy());
    peer.on("error", () => socket.destroy());
    socket.on("close", () => peer.destroy());
    peer.on("close", () => socket.destroy());
    socket.pipe(peer).pipe(socket);
  });
  up.on("error", () => socket.destroy());
  up.on("response", () => socket.destroy());
  up.end();
});
front.listen(0, "127.0.0.1");
await once(front, "listening");
const browser = await chromium.launch({
  channel: process.env.OW_TEST_BROWSER_CHANNEL || "chrome",
  args: [
    "--no-proxy-server",
    "--host-resolver-rules=" +
      [...routes.keys()].map((h) => "MAP " + h + " 127.0.0.1").join(","),
  ],
});
try {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const urls = slots.map((s) => s.url + ":" + front.address().port);
  const page = await ctx.newPage();
  assert.equal((await page.goto(urls[0])).status(), 403);
  await ctx.addCookies(
    urls.map((url) => ({
      url,
      name: "__Host-demo",
      value: session,
      secure: true,
      httpOnly: true,
      sameSite: "Strict",
    })),
  );
  const messages = [];
  page.on("websocket", (ws) =>
    ws.on("framereceived", (frame) => {
      try {
        messages.push(JSON.parse(String(frame.payload)).type);
      } catch {}
    }),
  );
  assert.equal((await page.goto(urls[0])).status(), 200);
  await page.waitForFunction(() => document.body.dataset.revision === "one");
  for (let i = 0; i < 50 && !messages.includes("connected"); i++)
    await delay(100);
  assert.ok(messages.includes("connected"), "Vite WebSocket connection");
  const started = await page.evaluate(() => performance.timeOrigin);
  const second = await ctx.newPage();
  await second.goto(urls[1]);
  assert.match(await second.locator("body").innerText(), /second port/);
  await writeFile(join(dir, "loaded.ok"), "");
  await checkpoint("changed");
  await page.waitForFunction(() => document.body.dataset.revision === "two");
  assert.equal(await page.evaluate(() => performance.timeOrigin), started);
  assert.ok(messages.includes("update"));
  await writeFile(join(dir, "changed.ok"), "");
  console.log(
    "PASS: isolated origins, browser assets and Vite HMR over one private gateway tunnel",
  );
} finally {
  await browser.close();
  front.close();
}
