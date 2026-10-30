// Container-level coding-tool smoke. Not an OpenShell/kernel-isolation claim.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
const image = process.argv[2];
assert(
  image && /^[A-Za-z0-9./_:@-]+$/.test(image),
  "Pass a trusted local image tag or immutable digest",
);
const child = spawn(
  "docker",
  [
    "run",
    "--rm",
    "-i",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "64",
    "--memory",
    "512m",
    "--tmpfs",
    "/sandbox:rw,size=64m",
    "--tmpfs",
    "/tmp:rw,size=64m",
    "--entrypoint",
    "/opt/openworker/openworker",
    image,
    "mcp-coding",
    "--workspace",
    "/sandbox",
  ],
  { stdio: ["pipe", "pipe", "pipe"] },
);
const pending = new Map();
let id = 0,
  stderr = "",
  exited = false;
child.stderr.on("data", (b) => {
  stderr = (stderr + b).slice(-8000);
});
child.on("error", (e) => {
  for (const p of pending.values()) p.reject(e);
});
child.on("exit", (code) => {
  exited = true;
  for (const p of pending.values())
    p.reject(Error(`Container exited ${code}: ${stderr}`));
});
const lines = createInterface({ input: child.stdout });
lines.on("line", (line) => {
  try {
    const r = JSON.parse(line);
    const p = pending.get(r.id);
    if (!p) return;
    pending.delete(r.id);
    clearTimeout(p.timer);
    r.error ? p.reject(Error("MCP protocol error")) : p.resolve(r.result);
  } catch (e) {
    for (const p of pending.values()) p.reject(e);
  }
});
function rpc(method, params = {}) {
  const n = ++id;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(n);
      reject(Error("Coding MCP deadline"));
    }, 20000);
    pending.set(n, { resolve, reject, timer });
    child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", id: n, method, params }) + "\n",
    );
  });
}
async function tool(name, args) {
  const r = await rpc("tools/call", { name, arguments: args });
  assert(!r.isError, JSON.stringify(r.content));
  return JSON.parse(r.content[0].text);
}
try {
  await rpc("initialize", {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: { name: "coding-qualification", version: "1" },
  });
  const catalog = await rpc("tools/list");
  for (const name of [
    "read_file",
    "write_file",
    "apply_patch",
    "process_start",
    "process_logs",
    "process_status",
    "process_stop",
    "git_status",
  ])
    assert(
      catalog.tools.some((t) => t.name === name),
      `Missing ${name}`,
    );
  await tool("write_file", {
    path: "smoke.txt",
    content: "coding runner smoke\n",
    create_only: true,
  });
  const file = await tool("read_file", { path: "smoke.txt" });
  assert(JSON.stringify(file).includes("coding runner smoke"));
  const shell = await tool("run_shell", {
    command:
      "node --version && python3 --version && git --version && curl --version | head -n 1",
    yield_ms: 10000,
    timeout_seconds: 20,
  });
  assert.equal(shell.exit_code, 0);
  assert(shell.stdout.includes("v24."));
  const process = await tool("process_start", {
    command: 'node -e "setInterval(() => {}, 1000)"',
    timeout_seconds: 30,
  });
  assert(process.process_id && process.status === "running");
  const status = await tool("process_status", {
    process_id: process.process_id,
  });
  assert.equal(status.status, "running");
  await tool("process_stop", { process_id: process.process_id });
  console.log(
    JSON.stringify({
      passed: true,
      scope: "network-disabled read-only container; no OpenShell claim",
      tools: catalog.tools.length,
      node24: true,
      managedProcessSurvivedToolCalls: true,
      hostCredentialsPassed: false,
    }),
  );
} finally {
  child.stdin.end();
  for (const p of pending.values()) clearTimeout(p.timer);
  lines.close();
  const timer = setTimeout(() => {
    if (!exited) child.kill("SIGKILL");
  }, 3000);
  timer.unref();
}
