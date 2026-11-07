// Loopback access to one owned local kind fixture. No agent submission or token renewal.
import assert from "node:assert/strict";
import { readFile, writeFile, open, mkdir, unlink } from "node:fs/promises";
import { spawn } from "node:child_process";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { run } from "./stack.mjs";
const [directory, apiPortArg, uiPortArg = "58660"] = process.argv.slice(2);
assert(directory, "Usage: local-views.mjs SERVICE_STATE [API_PORT] [UI_PORT]");
const root = resolve(directory),
  apiPort = Number(
    apiPortArg ??
      JSON.parse(await readFile(join(root, "kubernetes.json"))).port ??
      58444,
  ),
  uiPort = Number(uiPortArg);
assert(
  [apiPort, uiPort].every(
    (x) => Number.isInteger(x) && x >= 1024 && x <= 65535,
  ) && apiPort !== uiPort,
  "Distinct unprivileged ports required",
);
const m = JSON.parse(await readFile(join(root, "kubernetes.json")));
assert(
  m.context.startsWith("kind-") &&
    /^ow-service-m4-[a-z0-9]+$/.test(m.namespace),
  "Explicit local kind fixture required",
);
const ns = JSON.parse(
  await run("kubectl", [
    "--kubeconfig",
    m.kubeconfig,
    "--context",
    m.context,
    "get",
    "namespace",
    m.namespace,
    "-o",
    "json",
  ]),
);
assert.equal(
  ns.metadata.labels?.["openworker.io/qualification"],
  m.id,
  "Namespace ownership mismatch",
);
const privateRoot = join(root, "private");
await mkdir(privateRoot, { recursive: true, mode: 0o700 });
const lockPath = join(privateRoot, "views.lock");
await writeFile(
  lockPath,
  JSON.stringify({ pid: process.pid, serviceId: m.id }),
  { flag: "wx", mode: 0o600 },
);
const configPath = join(privateRoot, "ui.json");
await writeFile(
  configPath,
  JSON.stringify({
    serviceUrl: `https://127.0.0.1:${apiPort}`,
    serviceId: m.id,
    namespace: "local",
    port: uiPort,
    sessionSeconds: 3600,
  }),
  { mode: 0o600 },
);
let stopping = false;
const children = new Map(),
  timers = new Set();
async function launch(name, command, args, env = process.env, attempt = 0) {
  if (stopping) return;
  const log = await open(join(privateRoot, name + ".log"), "a", 0o600);
  const p = spawn(command, args, { env, stdio: ["ignore", log.fd, log.fd] });
  await log.close();
  children.set(name, p);
  p.on("error", () =>
    console.error(`${name} failed; inspect private fixture logs.`),
  );
  p.once("exit", () => {
    children.delete(name);
    if (stopping) return;
    const timer = setTimeout(
      () => {
        timers.delete(timer);
        launch(name, command, args, env, Math.min(attempt + 1, 5)).catch(fatal);
      },
      Math.min(1000 * 2 ** attempt, 15000),
    );
    timers.add(timer);
  });
}
async function stop() {
  if (stopping) return;
  stopping = true;
  for (const t of timers) clearTimeout(t);
  for (const c of children.values()) c.kill("SIGTERM");
  try {
    const lock = JSON.parse(await readFile(lockPath));
    if (lock.pid === process.pid && lock.serviceId === m.id)
      await unlink(lockPath);
  } catch {}
}
function fatal() {
  console.error("Local views failed; inspect private logs.");
  process.exitCode = 1;
  void stop();
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
await launch("api-forward", "kubectl", [
  "--kubeconfig",
  m.kubeconfig,
  "--context",
  m.context,
  "-n",
  m.namespace,
  "port-forward",
  "service/ows-api",
  `${apiPort}:443`,
  "--address",
  "127.0.0.1",
]);
await launch(
  "management-ui",
  process.execPath,
  [
    fileURLToPath(
      new URL("../../harness/service-ui/server.mjs", import.meta.url),
    ),
    configPath,
  ],
  { ...process.env, NODE_EXTRA_CA_CERTS: join(root, "input/api-ca.crt") },
);
console.log(
  JSON.stringify({
    serviceId: m.id,
    namespace: m.namespace,
    api: `https://127.0.0.1:${apiPort}`,
    management: `http://127.0.0.1:${uiPort}/#observability`,
    access: "loopback only",
    credentialRenewal: false,
  }),
);
