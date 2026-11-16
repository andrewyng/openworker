// Real Electron -> TypeScript SDK -> Rust child -> local model fixture -> callback.
// Uses no live model credential. The preview deliberately attempts forbidden access.
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
const appRoot = resolve(process.argv[2] ?? "");
if (!process.argv[2])
  throw Error("Pass a generated Electron starter directory");
const playwright = await import(
  process.env.OW_PLAYWRIGHT_MODULE ?? "playwright"
);
const require = createRequire(join(appRoot, "package.json"));
let requests = 0,
  forbidden = 0;
const server = createServer(async (req, res) => {
  if (req.url !== "/v1/chat/completions") {
    forbidden++;
    res.writeHead(403);
    res.end();
    return;
  }
  let bytes = "";
  for await (const part of req) bytes += part;
  const input = JSON.parse(bytes);
  requests++;
  assert.equal(req.headers.authorization, undefined);
  const finished = input.messages.some((m) => m.role === "tool");
  const html = `<!doctype html><html><head><style>body{margin:0;height:100vh;background:#151329;color:#f6f2ff;font:20px system-ui;display:grid;place-content:center;text-align:center}h1{font-size:44px;letter-spacing:-2px}button{padding:16px 24px;border:0;border-radius:14px;background:#b1a0ff;font:18px system-ui;color:#211b3d}p{color:#bdb5d6}</style></head><body><h1>Tiny Arcade</h1><p>A playable integration check.</p><button id="score">Score a point · 0</button><script>let count=0;document.getElementById('score').onclick=()=>document.getElementById('score').textContent='Score a point · '+(++count);window.proof={bridge:typeof window.arcade,node:typeof require,parentBlocked:false,networkBlocked:false};try{void parent.document.body}catch{window.proof.parentBlocked=true}fetch('http://127.0.0.1:${server.address().port}/forbidden').catch(()=>window.proof.networkBlocked=true);</script></body></html>`;
  const delta = finished
    ? { content: "Your game is ready." }
    : {
        tool_calls: [
          {
            index: 0,
            id: "call-preview-1",
            type: "function",
            function: {
              name: input.tools[0].function.name,
              arguments: JSON.stringify({ html }),
            },
          },
        ],
      };
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  res.write(
    "data: " +
      JSON.stringify({
        id: "fixture-" + requests,
        choices: [{ index: 0, delta, finish_reason: null }],
      }) +
      "\n\n",
  );
  res.end(
    "data: " +
      JSON.stringify({
        id: "fixture-" + requests,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: finished ? "stop" : "tool_calls",
          },
        ],
      }) +
      "\n\ndata: [DONE]\n\n",
  );
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const vite = spawn(
  process.execPath,
  [
    join(appRoot, "node_modules/vite/bin/vite.js"),
    "--host",
    "127.0.0.1",
    "--port",
    "5173",
  ],
  { cwd: appRoot, stdio: "pipe" },
);
let electron;
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch("http://127.0.0.1:5173")).ok) break;
    } catch {}
    if (i === 99) throw Error("Vite did not start");
    await new Promise((r) => setTimeout(r, 100));
  }
  electron = await playwright._electron.launch({
    executablePath: process.env.OW_PACKAGED_APP ?? require("electron"),
    args: process.env.OW_PACKAGED_APP ? [] : [appRoot],
    cwd: appRoot,
    env: { ...process.env, ARCADE_DEV_URL: "http://127.0.0.1:5173" },
    timeout: 60000,
  });
  const page = await electron.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.getByRole("button", { name: "New game", exact: true }).click();
  await page.getByRole("button", { name: "Model settings" }).click();
  await page
    .getByLabel("Provider", { exact: true })
    .fill(`TestLocal${server.address().port}`);
  await page
    .getByLabel("API", { exact: true })
    .selectOption("chat-completions");
  await page
    .getByLabel("Base URL")
    .fill(`http://127.0.0.1:${server.address().port}/v1`);
  await page.getByLabel("Model ID").fill("fixture-game");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByLabel("Describe your game").fill("Build a tiny arcade game.");
  await page.getByRole("button", { name: "Create ↗", exact: true }).click();
  await page
    .getByRole("button", { name: "Allow once", exact: true })
    .waitFor({ timeout: 60000 });
  assert.equal(
    await page.locator("iframe").count(),
    0,
    "No preview before approval",
  );
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  const preview = page.frameLocator("iframe");
  await preview
    .getByRole("button", { name: "Score a point · 0", exact: true })
    .click({ timeout: 60000 });
  await preview
    .getByRole("button", { name: "Score a point · 1", exact: true })
    .waitFor();
  const frame = page
    .frames()
    .find((frame) => frame.url().startsWith("arcade-preview:"));
  assert.ok(frame, "Isolated preview document");
  await frame.waitForFunction(() => window.proof?.networkBlocked === true);
  assert.deepEqual(await frame.evaluate(() => window.proof), {
    bridge: "undefined",
    node: "undefined",
    parentBlocked: true,
    networkBlocked: true,
  });
  assert.equal(
    forbidden,
    0,
    "CSP prevented outbound request before it reached the server",
  );
  await page
    .getByText("Your game is ready.", { exact: true })
    .waitFor({ timeout: 30000 });
  const evidence = join(appRoot, "qualification");
  await mkdir(evidence, { recursive: true });
  await page.screenshot({
    path: join(evidence, "desktop.png"),
    fullPage: true,
  });
  console.log(
    JSON.stringify({
      passed: true,
      requests,
      approvalBeforeExecution: true,
      previewScripts: true,
      privilegedBridge: false,
      network: false,
      screenshot: join(evidence, "desktop.png"),
    }),
  );
} catch (error) {
  const evidence = join(appRoot, "qualification");
  await mkdir(evidence, { recursive: true });
  const page = electron?.windows()[0];
  if (page) {
    await page
      .screenshot({ path: join(evidence, "failure.png"), fullPage: true })
      .catch(() => {});
    await writeFile(join(evidence, "failure.html"), await page.content()).catch(
      () => {},
    );
  }
  throw error;
} finally {
  await electron?.close();
  vite.kill();
  server.close();
}
