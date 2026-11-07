// Supervise read-only loopback access to the persistent local demo. Restarting
// port-forwards never resubmits an agent command or retries a tool action.
import assert from "node:assert/strict";
import { readFile, writeFile, open, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { run } from "../service/stack.mjs";
const root = resolve(process.argv[2] || "");
assert(process.argv[2], "Usage: observability-local-views.mjs STATE");
const m = JSON.parse(await readFile(join(root, "service/kubernetes.json"))),
  lf = JSON.parse(await readFile(join(root, "langfuse/installation.json")));
assert(
  m.context.startsWith("kind-") && m.context === lf.context,
  "Local kind contexts must match",
);
for (const [state, label] of [
  [m, "openworker.io/qualification"],
  [lf, "openworker.io/local-langfuse"],
]) {
  const ns = JSON.parse(
    await run("kubectl", [
      "--kubeconfig",
      state.kubeconfig,
      "--context",
      state.context,
      "get",
      "namespace",
      state.namespace,
      "-o",
      "json",
    ]),
  );
  assert.equal(
    ns.metadata.labels[label],
    state.id,
    "Namespace ownership mismatch",
  );
}
const ports = { api: 58450, ui: 58650, langfuse: lf.port };
await mkdir(join(root, "private"), { recursive: true, mode: 0o700 });
const uiConfig = join(root, "private/ui.json");
await writeFile(
  uiConfig,
  JSON.stringify({
    serviceUrl: `https://127.0.0.1:${ports.api}`,
    serviceId: m.id,
    namespace: "local",
    port: ports.ui,
    sessionSeconds: 3600,
  }),
  { mode: 0o600 },
);
let stopping = false;
const children = new Map(),
  timers = new Set();
async function journal() {
  await writeFile(
    join(root, "private/views-processes.json"),
    JSON.stringify(
      {
        pid: process.pid,
        children: Object.fromEntries([...children].map(([k, p]) => [k, p.pid])),
        ports,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
}
async function launch(name, bin, args, env = process.env, attempt = 0) {
  if (stopping) return;
  const log = await open(join(root, "private", name + ".log"), "a", 0o600);
  const child = spawn(bin, args, { env, stdio: ["ignore", log.fd, log.fd] });
  await log.close();
  children.set(name, child);
  await journal();
  child.on("error", () =>
    console.error(name + " could not start; inspect private local logs."),
  );
  child.once("exit", () => {
    children.delete(name);
    if (stopping) return;
    const timer = setTimeout(
      () => {
        timers.delete(timer);
        launch(name, bin, args, env, Math.min(attempt + 1, 5)).catch(fatal);
      },
      Math.min(1000 * 2 ** attempt, 15000),
    );
    timers.add(timer);
  });
}
function stop() {
  if (stopping) return;
  stopping = true;
  for (const t of timers) clearTimeout(t);
  for (const child of children.values()) child.kill("SIGTERM");
}
function fatal() {
  console.error("Local view supervision failed; inspect private logs.");
  stop();
  process.exitCode = 1;
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
const forward = (s, service, port, target) => [
  "--kubeconfig",
  s.kubeconfig,
  "--context",
  s.context,
  "-n",
  s.namespace,
  "port-forward",
  "service/" + service,
  `${port}:${target}`,
  "--address",
  "127.0.0.1",
];
await launch("api-forward", "kubectl", forward(m, "ows-api", ports.api, 443));
await launch(
  "langfuse-forward",
  "kubectl",
  forward(lf, "lf-web", ports.langfuse, 3000),
);
await launch(
  "management-ui",
  process.execPath,
  [
    fileURLToPath(
      new URL("../../harness/service-ui/server.mjs", import.meta.url),
    ),
    uiConfig,
  ],
  {
    ...process.env,
    NODE_EXTRA_CA_CERTS: join(root, "service/input/api-ca.crt"),
  },
);
console.log(
  JSON.stringify({
    management: `http://127.0.0.1:${ports.ui}`,
    langfuse: `http://127.0.0.1:${ports.langfuse}`,
    access: "loopback only",
    state: root,
  }),
);
