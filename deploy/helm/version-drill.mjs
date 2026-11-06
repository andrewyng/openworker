// Isolated kind upgrade drill. Requires two locally loaded image digests with
// identical tool contracts but DIFFERENT bundled coworker instructions/identity.
// Never uses local.mjs's live namespace or regenerates its credentials.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomUUID, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { run, kubectl, repo } from "./local.mjs";
const input = JSON.parse(await readFile(resolve(process.argv[2]), "utf8"));
const {
  kubeconfig,
  context,
  helm,
  imageA,
  imageB,
  runnerImage,
  certificates,
  gateway,
} = input;
assert.equal(context, "kind-ow-server-20261011");
for (const image of [imageA, imageB, runnerImage])
  assert.match(image, /@sha256:[a-f0-9]{64}$/);
const m = { kubeconfig, context },
  ns = "ow-version-" + randomBytes(4).toString("hex"),
  root = join(repo, ".ow-stack", ns),
  id = randomUUID(),
  checks = [],
  forwards = [];
await mkdir(root, { recursive: true, mode: 0o700 });
const save = (name, v) =>
  writeFile(
    join(root, name),
    typeof v === "string" ? v : JSON.stringify(v, null, 2),
    { mode: 0o600 },
  );
const k = (args, body) => kubectl(m, ["-n", ns, ...args], body);
const apply = (items) =>
  k(
    ["apply", "-f", "-"],
    JSON.stringify({ apiVersion: "v1", kind: "List", items }),
  );
const meta = (name) => ({ name, namespace: ns });
const secret = (name, stringData) => ({
  apiVersion: "v1",
  kind: "Secret",
  metadata: meta(name),
  stringData,
});
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
const wait = async (fn, ms = 120000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw Error("drill state deadline");
};
const token = randomBytes(32).toString("hex"),
  password = randomBytes(32).toString("hex");
let environment;
await save("ownership.json", { ns, id, context, kubeconfig });
async function certificate(name, dns) {
  const key = join(root, name + ".key"),
    crt = join(root, name + ".crt");
  await run("openssl", [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "2",
    "-keyout",
    key,
    "-out",
    crt,
    "-subj",
    "/CN=" + dns,
    "-addext",
    "subjectAltName=DNS:" + dns,
    "-addext",
    "basicConstraints=critical,CA:TRUE",
  ]);
  return { key: await readFile(key, "utf8"), crt: await readFile(crt, "utf8") };
}
const pg = await certificate("pg", `postgres.${ns}.svc.cluster.local`),
  tls = await certificate("api", "localhost");
const shell = {};
for (const name of ["ca.crt", "tls.crt", "tls.key"]) {
  shell[name] = await readFile(join(certificates, name), "utf8");
}
const config = {
  database_env: "OW_SERVER_DATABASE_URL",
  schema: "ow_versions",
  database_ca: "/database-ca/ca.crt",
  state_root: "/state",
  bundle: "/opt/openworker/bundle",
  runner: "/opt/openworker/openworker",
  model: "fixture",
  scripted: true,
  api_token_file: "/bootstrap/admin",
  actor: "version-drill",
  openshell: {
    identity: "version-drill-" + id,
    endpoint: gateway,
    certificates: "/openshell",
    tls_server_name: "localhost",
    workspace: "default",
    image: runnerImage,
  },
};
const values = (image, replicas = 1) => ({
  image,
  api: { replicas },
  worker: {
    replicas,
    slots: 1,
    drainTimeoutSeconds: 60,
    shutdownTimeoutSeconds: 30,
  },
  terminationGracePeriodSeconds: 110,
  resources: {
    requests: { cpu: "100m", memory: "128Mi" },
    limits: { cpu: "1", memory: "768Mi" },
  },
  config,
  secrets: {
    database: "database",
    databaseCA: "database-ca",
    openshell: "openshell-client",
    apiTLS: "api-tls",
    probe: "probe",
  },
  network: { enabled: false },
});
async function install(release, image, replicas = 1, override = {}) {
  const path = join(root, release + ".json");
  await save(release + ".json", { ...values(image, replicas), ...override });
  await run(helm, [
    "upgrade",
    "--install",
    release,
    join(repo, "deploy/helm/openworker"),
    "--kubeconfig",
    kubeconfig,
    "--kube-context",
    context,
    "-n",
    ns,
    "-f",
    path,
  ]);
}
async function admin(release, args) {
  const dep = JSON.parse(
    await k([
      "get",
      "deployment",
      release + "-openworker-worker",
      "-o",
      "json",
    ]),
  );
  const spec = dep.spec.template.spec,
    c = spec.containers[0];
  c.args = ["--config", "/config/reference.json", ...args];
  delete c.readinessProbe;
  delete c.livenessProbe;
  delete c.envFrom;
  c.env = c.env.filter((e) => e.name !== "POD_NAME");
  c.volumeMounts.push({
    name: "bootstrap",
    mountPath: "/bootstrap/admin",
    subPath: "admin",
    readOnly: true,
  });
  spec.volumes.push({
    name: "bootstrap",
    secret: { secretName: "bootstrap", defaultMode: 288 },
  });
  spec.restartPolicy = "Never";
  const name = "admin-" + randomBytes(4).toString("hex");
  await apply([
    {
      apiVersion: "batch/v1",
      kind: "Job",
      metadata: meta(name),
      spec: { backoffLimit: 0, template: { spec } },
    },
  ]);
  await k([
    "wait",
    "--for=condition=complete",
    "job/" + name,
    "--timeout=150s",
  ]);
  return k(["logs", "job/" + name]);
}
async function forward(release) {
  const ps = JSON.parse(
    await k([
      "get",
      "pods",
      "-l",
      `app.kubernetes.io/instance=${release},app.kubernetes.io/component=api`,
      "-o",
      "json",
    ]),
  ).items;
  const pod = ps.find(
    (p) =>
      !p.metadata.deletionTimestamp &&
      p.status.containerStatuses?.every((c) => c.ready),
  );
  assert.ok(pod);
  const child = spawn(
    "kubectl",
    [
      "--kubeconfig",
      kubeconfig,
      "--context",
      context,
      "-n",
      ns,
      "port-forward",
      "--address",
      "127.0.0.1",
      "pod/" + pod.metadata.name,
      "0:8080",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  forwards.push(child);
  child.stderr.resume();
  return new Promise((resolve, reject) => {
    let out = "";
    const timer = setTimeout(() => reject(Error("forward timeout")), 15000);
    child.stdout.on("data", (b) => {
      out += b;
      const m = out.match(/127.0.0.1:(\d+) ->/);
      if (m) {
        clearTimeout(timer);
        resolve("http://127.0.0.1:" + m[1]);
      }
    });
    child.on("error", reject);
  });
}
async function request(base, path, body) {
  const r = await fetch(base + path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(20000),
  });
  assert.ok(r.ok, `${path} ${r.status}`);
  return r.json();
}
const create = async (base) =>
  (
    await request(base, "/sessions", {
      request_id: randomUUID(),
      parameters: {
        name: "Version qualification",
        coworker: "assistant-example",
        workspace: "/sandbox",
        environment_id: environment.id,
      },
    })
  ).session_id;
const turn = (base, sid, name, args) =>
  request(base, `/sessions/${sid}/turns`, {
    submission_id: randomUUID(),
    text: "TEST_RESOURCE_GRANTS " + JSON.stringify([{ name, arguments: args }]),
  });
const snap = (base, sid) => request(base, `/sessions/${sid}`);
const state = (base, sid, status) =>
  wait(async () => {
    const s = await snap(base, sid);
    assert.ok(!["failed", "truncated"].includes(s.turn?.status));
    return s.turn?.status === status && s;
  });
const approve = (base, sid, s) => {
  const i = s.interactions.find((i) => !i.response);
  assert.ok(i);
  return request(base, `/sessions/${sid}/controls`, {
    request_id: randomUUID(),
    turn_id: i.turn_id,
    action: {
      kind: "response",
      interaction_id: i.id,
      response: "once",
      binding: Object.fromEntries(
        [
          "bundle_digest",
          "schema_digest",
          "policy_digest",
          "resource_policy_digest",
        ].map((k) => [k, i[k]]),
      ),
    },
  });
};
const rollout = (release, role = "worker") =>
  k([
    "rollout",
    "status",
    `deployment/${release}-openworker-${role}`,
    "--timeout=180s",
  ]);
try {
  await kubectl(m, ["create", "namespace", ns]);
  await kubectl(m, [
    "annotate",
    "namespace",
    ns,
    "openworker.dev/version-drill=" + id,
  ]);
  await apply([
    secret("database", {
      url: `postgresql://openworker:${password}@postgres.${ns}.svc.cluster.local/openworker`,
    }),
    secret("postgres-login", {
      POSTGRES_USER: "openworker",
      POSTGRES_DB: "openworker",
      POSTGRES_PASSWORD: password,
    }),
    secret("database-ca", { "ca.crt": pg.crt }),
    secret("postgres-tls", { "tls.crt": pg.crt, "tls.key": pg.key }),
    secret("api-tls", { "tls.crt": tls.crt, "tls.key": tls.key }),
    secret("openshell-client", shell),
    secret("bootstrap", { admin: token }),
    secret("probe", {
      "curl.conf": `header = "Authorization: Bearer ${token}"\n`,
    }),
    {
      apiVersion: "v1",
      kind: "Service",
      metadata: meta("postgres"),
      spec: { selector: { app: "postgres" }, ports: [{ port: 5432 }] },
    },
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: meta("postgres"),
      spec: {
        replicas: 1,
        selector: { matchLabels: { app: "postgres" } },
        template: {
          metadata: { labels: { app: "postgres" } },
          spec: {
            containers: [
              {
                name: "postgres",
                image:
                  "postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
                command: [
                  "sh",
                  "-ec",
                  "cp /cert/* /tmp/; chown postgres:postgres /tmp/tls.*; chmod 600 /tmp/tls.key; exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/tls.crt -c ssl_key_file=/tmp/tls.key",
                ],
                envFrom: [{ secretRef: { name: "postgres-login" } }],
                resources: { limits: { cpu: "1", memory: "1Gi" } },
                readinessProbe: {
                  exec: { command: ["pg_isready", "-U", "openworker"] },
                  periodSeconds: 2,
                },
                volumeMounts: [
                  { name: "cert", mountPath: "/cert" },
                  { name: "data", mountPath: "/var/lib/postgresql/data" },
                ],
              },
            ],
            volumes: [
              { name: "cert", secret: { secretName: "postgres-tls" } },
              { name: "data", emptyDir: {} },
            ],
          },
        },
      },
    },
  ]);
  await k(["rollout", "status", "deployment/postgres", "--timeout=90s"]);
  await install("old", imageA, 0);
  await admin("old", ["migrate"]);
  await admin("old", ["auth-bootstrap"]);
  environment = JSON.parse(
    (
      await admin("old", ["environment-create", "--request-id", id, "--shared"])
    ).trim(),
  );
  await save("environment.json", environment);
  await install("old", imageA);
  await rollout("old");
  await rollout("old", "api");
  await install("bad", imageA, 1, {
    api: { replicas: 0 },
    config: {
      ...config,
      openshell: { ...config.openshell, endpoint: "https://127.0.0.1:9" },
    },
  });
  await wait(async () => {
    const pods = JSON.parse(
      await k([
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/instance=bad,app.kubernetes.io/component=worker",
        "-o",
        "json",
      ]),
    ).items;
    return pods.some((p) =>
      p.status.containerStatuses?.some((c) => c.restartCount > 0 && !c.ready),
    );
  });
  assert.ok(
    !JSON.parse(await admin("old", ["worker-pools"])).pools.some((p) =>
      p.workers.some((w) => w.node.startsWith("bad-")),
    ),
  );
  await run(helm, [
    "uninstall",
    "bad",
    "--kubeconfig",
    kubeconfig,
    "--kube-context",
    context,
    "-n",
    ns,
  ]);
  pass(
    "Unavailable gateway cannot pass readiness or advertise a usable worker",
  );
  let a = await forward("old");
  const parked = await create(a);
  await turn(a, parked, "write_file", {
    path: "old.txt",
    content: "old identity retained",
  });
  const pending = await state(a, parked, "waiting"),
    binding = pending.interactions[0].bundle_digest;
  pass("Old cohort parked at a durable approval");
  await install("new", imageB);
  await rollout("new");
  await rollout("new", "api");
  let b = await forward("new");
  const fresh = await create(b);
  await turn(b, fresh, "write_file", {
    path: "new.txt",
    content: "new identity",
  });
  const newPending = await state(b, fresh, "waiting");
  assert.notEqual(newPending.interactions[0].bundle_digest, binding);
  await approve(b, fresh, newPending);
  await state(b, fresh, "completed");
  assert.deepEqual((await snap(b, parked)).interactions, pending.interactions);
  pass(
    "Side-by-side pools execute only their exact identities; new API reads old approval unchanged",
  );
  // Prove missing old pool is visible and new pool cannot consume its answer.
  await k(["scale", "deployment/old-openworker-worker", "--replicas=0"]);
  await wait(
    async () =>
      !JSON.parse(
        await k([
          "get",
          "pods",
          "-l",
          "app.kubernetes.io/instance=old,app.kubernetes.io/component=worker",
          "-o",
          "json",
        ]),
      ).items.length,
  );
  await approve(b, parked, pending);
  await turn(b, parked, "read_file", { path: "old.txt" });
  let report = JSON.parse(await admin("new", ["worker-pools"]));
  const old = report.pools.find((p) => p.execution_digest === binding);
  assert.equal(old.pending_controls, 1);
  assert.equal(old.queued_turns, 1);
  assert.equal(old.sessions_without_advertised_worker, 1);
  assert.deepEqual((await snap(b, parked)).interactions, pending.interactions);
  pass(
    "Removing old pool leaves approval and queued follow-up intact; catalog flags uncovered session",
  );
  await k(["scale", "deployment/old-openworker-worker", "--replicas=1"]);
  await rollout("old");
  await wait(async () => {
    const s = await snap(b, parked);
    return s.turn?.status === "waiting" && s.turn.id !== pending.turn.id;
  });
  // This bundled MCP read is still external: previous write approval does not
  // authorize a distinct read call. Answer the follow-up's own durable prompt.
  await approve(b, parked, await snap(b, parked));
  await state(b, parked, "completed");
  pass(
    "Restoring pinned old pool applies approval and queued follow-up without identity rewrite",
  );
  // Active execution finishes during a genuine Deployment rolling restart.
  const active = await create(a);
  await turn(a, active, "run_shell", {
    command: "sleep 20; printf 'one\\n' >> rollout-effects.txt",
  });
  const ap = await state(a, active, "waiting");
  await approve(a, active, ap);
  await state(a, active, "executing");
  const oldWorker = JSON.parse(
    await k([
      "get",
      "pods",
      "-l",
      "app.kubernetes.io/instance=old,app.kubernetes.io/component=worker",
      "-o",
      "json",
    ]),
  ).items.find((p) => !p.metadata.deletionTimestamp).metadata.name;
  await k(["rollout", "restart", "deployment/old-openworker-worker"]);
  await wait(
    async () =>
      JSON.parse(await k(["get", "pod", oldWorker, "-o", "json"])).metadata
        .deletionTimestamp,
  );
  assert.equal((await snap(b, active)).turn.status, "executing");
  assert.equal(
    await k([
      "exec",
      oldWorker,
      "--",
      "curl",
      "--silent",
      "--output",
      "/dev/null",
      "--write-out",
      "%{http_code}",
      "--max-time",
      "2",
      "http://127.0.0.1:8081/readyz",
    ]),
    "503",
  );
  await state(b, active, "completed");
  await rollout("old");
  assert.equal(
    await kubectl(m, [
      "-n",
      "openshell",
      "exec",
      "default--" + environment.binding.remote_name,
      "-c",
      "agent",
      "--",
      "cat",
      "/sandbox/rollout-effects.txt",
    ]),
    "one\n",
  );
  pass(
    "Kubernetes rolling restart marks draining worker unready, finishes active execution exactly once and starts a ready replacement",
  );
  // API creation rollback restores old identity; both worker pools stay present.
  await k([
    "set",
    "image",
    "deployment/new-openworker-api",
    "harness=" + imageA,
  ]);
  await rollout("new", "api");
  b = await forward("new");
  const rollback = await create(b);
  await turn(b, rollback, "write_file", {
    path: "rollback.txt",
    content: "rollback",
  });
  const rp = await state(b, rollback, "waiting");
  assert.equal(rp.interactions[0].bundle_digest, binding);
  // New-version idle sessions still exist: their worker pool MUST remain.
  await k(["rollout", "undo", "deployment/new-openworker-api"]);
  await rollout("new", "api");
  await approve(a, rollback, rp);
  await state(a, rollback, "completed");
  report = JSON.parse(await admin("old", ["worker-pools"]));
  assert.equal(report.pools.length, 2);
  assert.ok(
    report.pools.every((p) => p.sessions_without_advertised_worker === 0),
  );
  pass(
    "Creation rollback uses old identity; both cohorts retained for resumable sessions",
  );
  await save("report.json", {
    checks,
    namespace: ns,
    context,
    imageA,
    imageB,
    report,
    live_model: false,
    scope: "local kind; synthetic model; real PostgreSQL/OpenShell/tools",
  });
  console.log("REPORT " + join(root, "report.json"));
} finally {
  for (const child of forwards) child.kill("SIGTERM");
  // Keep exact owned resources on failure for diagnosis. Cleanup is a separate
  // explicit operator action after inspecting report and environment identity.
  await save("checks.json", {
    checks,
    environment_id: environment?.id,
    namespace: ns,
  });
}
