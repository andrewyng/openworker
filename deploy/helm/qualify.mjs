// Real TLS / Kubernetes / PostgreSQL / OpenShell / S3, scripted model only.
import assert from "node:assert/strict";
import https from "node:https";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID, randomBytes, createHash } from "node:crypto";
import {
  settings,
  kubectl,
  namespace,
  shellNamespace,
  root,
  repo,
} from "./local.mjs";
const m = await settings(),
  ca = await readFile(join(root, "private/api.ca.crt")),
  admin = (await readFile(join(root, "private/admin"), "utf8")).trim();
const env = JSON.parse(await readFile(join(root, "environment.json"), "utf8"));
const forwards = [],
  checks = [];
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
async function pods(role) {
  return JSON.parse(
    await kubectl(m, [
      "-n",
      namespace,
      "get",
      "pods",
      "-l",
      `app.kubernetes.io/instance=ow,app.kubernetes.io/component=${role}`,
      "-o",
      "json",
    ]),
  ).items.filter(
    (p) =>
      p.metadata.ownerReferences?.some((o) => o.kind === "ReplicaSet") &&
      !p.metadata.deletionTimestamp &&
      p.status.containerStatuses?.every((c) => c.ready),
  );
}
async function forward(pod) {
  const child = spawn(
    "kubectl",
    [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "-n",
      namespace,
      "port-forward",
      "--address",
      "127.0.0.1",
      "pod/" + pod,
      "0:8443",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  forwards.push(child);
  child.stderr.resume();
  return new Promise((resolve, reject) => {
    let s = "";
    const timer = setTimeout(
      () => reject(Error("port-forward timeout")),
      15000,
    );
    child.stdout.on("data", (b) => {
      s += b;
      const match = s.match(/127\.0\.0\.1:(\d+) ->/);
      if (match) {
        clearTimeout(timer);
        resolve(Number(match[1]));
      }
    });
    child.on("error", reject);
    child.on("exit", () => {
      clearTimeout(timer);
      reject(Error("port-forward ended"));
    });
  });
}
function request(
  port,
  path,
  { token = admin, method = "GET", body, trust = ca, stream = false } = {},
) {
  return new Promise((resolve, reject) => {
    const bytes =
      body === undefined
        ? undefined
        : Buffer.isBuffer(body)
          ? body
          : Buffer.from(JSON.stringify(body));
    const req = https.request(
      {
        hostname: "localhost",
        family: 4,
        port,
        path,
        method,
        ca: trust,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(bytes
            ? {
                "Content-Type": "application/json",
                "Content-Length": bytes.length,
              }
            : {}),
        },
      },
      (res) => {
        if (stream) {
          resolve(res);
          return;
        }
        const chunks = [];
        res.on("data", (b) => chunks.push(b));
        res.on("error", reject);
        res.on("end", () =>
          resolve({ status: res.statusCode, bytes: Buffer.concat(chunks) }),
        );
      },
    );
    req.setTimeout(20000, () => req.destroy(Error("request timed out")));
    req.on("error", reject);
    req.end(bytes);
  });
}
async function json(port, path, options = {}) {
  const r = await request(port, path, options);
  assert.ok(r.status >= 200 && r.status < 300, `${path}: ${r.status}`);
  return JSON.parse(r.bytes);
}
const wait = async (fn, ms = 90000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw Error("state deadline exceeded");
};
let a, b, operator, session;
const snap = () => json(b, `/sessions/${session}`, { token: operator });
const state = (s) =>
  wait(async () => {
    const v = await snap();
    assert.ok(
      !["failed", "truncated"].includes(v.turn?.status),
      JSON.stringify(v.turn),
    );
    return v.turn?.status === s && v;
  });
const post = (port, path, body, token = operator) =>
  json(port, path, { token, method: "POST", body });
async function approve(s) {
  const i = s.interactions.find((i) => !i.response);
  assert.ok(i);
  return post(b, `/sessions/${session}/controls`, {
    request_id: "answer-" + i.id,
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
}
const plan = (name, args) =>
  "TEST_RESOURCE_GRANTS " + JSON.stringify([{ name, arguments: args }]);
async function events(through) {
  const res = await request(a, `/sessions/${session}/events?after=0`, {
    token: operator,
    stream: true,
  });
  assert.equal(res.statusCode, 200);
  const timer = setTimeout(() => res.destroy(Error("SSE timeout")), 20000),
    out = [];
  let buffer = "";
  try {
    for await (const chunk of res) {
      buffer += chunk.toString();
      let at;
      while ((at = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, at);
        buffer = buffer.slice(at + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (line) {
          const e = JSON.parse(line.slice(6));
          assert.equal(e.event_id, out.length + 1);
          out.push(e);
          if (e.event_id >= through) return out;
        }
      }
    }
    throw Error("SSE closed early");
  } finally {
    clearTimeout(timer);
    res.destroy();
  }
}
try {
  const apis = await wait(async () => {
    const ps = await pods("api");
    return ps.length === 2 && ps;
  });
  a = await forward(apis[0].metadata.name);
  b = await forward(apis[1].metadata.name);
  assert.equal((await request(a, "/sessions", { token: "" })).status, 401);
  await assert.rejects(
    request(a, "/healthz", {
      trust: await readFile(join(root, "private/objects.crt")),
    }),
  );
  assert.equal(
    (
      await request(b, "/healthz", {
        token: (await readFile(join(root, "private/probe"), "utf8")).trim(),
      })
    ).status,
    200,
  );
  assert.equal(
    (
      await request(a, "/sessions", {
        token: (await readFile(join(root, "private/probe"), "utf8")).trim(),
        method: "POST",
        body: {},
      })
    ).status,
    403,
  );
  pass(
    "Both API pods enforce bearer roles through verified TLS; wrong CA rejected",
  );
  const grpc = await kubectl(
    m,
    [
      "-n",
      namespace,
      "exec",
      "-i",
      apis[0].metadata.name,
      "-c",
      "harness",
      "--",
      "curl",
      "--silent",
      "--show-error",
      "--include",
      "--max-time",
      "10",
      "--http2",
      "--cacert",
      "/openshell/ca.crt",
      "-H",
      "Content-Type: application/grpc",
      "-H",
      "TE: trailers",
      "--data-binary",
      "@-",
      `https://ow-helm-gateway.${shellNamespace}.svc.cluster.local:8080/openshell.v1.OpenShell/ListSandboxes`,
    ],
    Buffer.alloc(5),
  );
  assert.match(grpc, /grpc-status: 16/i);
  pass(
    "OpenShell refuses a caller without its client certificate; authenticated environment creation succeeded",
  );
  operator = randomBytes(32).toString("hex");
  const credential = await post(
    a,
    "/credentials",
    {
      request_id: randomUUID(),
      token: operator,
      spec: {
        principal: "helm-app-" + randomUUID(),
        role: "operator",
        scope: { kind: "owned" },
        environments: [env.id],
      },
    },
    admin,
  );
  const made = await post(a, "/sessions", {
    request_id: randomUUID(),
    parameters: {
      name: "Kubernetes deployment proof",
      coworker: "assistant-example",
      workspace: "/sandbox",
      environment_id: env.id,
    },
  });
  session = made.session_id;
  const file = "helm-" + randomUUID() + ".txt",
    proof =
      "Verified Kubernetes deployment: approval, restart and durable result.";
  await post(a, `/sessions/${session}/turns`, {
    submission_id: randomUUID(),
    text: plan("write_file", { path: file, content: proof }),
  });
  const pending = await state("waiting"),
    interaction = pending.interactions.find((i) => !i.response);
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "restart",
    "deployment/ow-openworker-worker",
  ]);
  await kubectl(m, ["-n", namespace, "delete", "pod", apis[0].metadata.name]);
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "status",
    "deployment/ow-openworker-worker",
    "--timeout=120s",
  ]);
  const replacement = await wait(async () => {
    const ps = await pods("api");
    return ps.find(
      (p) => !apis.some((old) => old.metadata.uid === p.metadata.uid),
    );
  });
  a = await forward(replacement.metadata.name);
  const restored = await snap();
  assert.deepEqual(
    restored.interactions.find((i) => i.id === interaction.id),
    interaction,
  );
  await approve(restored);
  await state("completed");
  pass(
    "Pending approval survives worker rollout and API-pod replacement, then resumes through another API",
  );
  await post(a, `/sessions/${session}/turns`, {
    submission_id: randomUUID(),
    text: plan("read_file", { path: file }),
  });
  await approve(await state("waiting"));
  const completed = await state("completed");
  const log = await events(completed.cursor),
    result = log
      .filter((e) => e.type === "tool_finished" && e.data.name === "read_file")
      .at(-1).data.result;
  const bytes = Buffer.from(JSON.parse(result.content[0].text).text);
  assert.equal(bytes.toString(), proof);
  const hash = createHash("sha256").update(bytes).digest("hex");
  const artifact = await post(a, `/sessions/${session}/artifacts`, {
    request_id: randomUUID(),
    turn_id: completed.turn.id,
    name: file,
    content_type: "text/plain",
    size: bytes.length,
    sha256: hash,
  });
  const content = `/sessions/${session}/artifacts/${artifact.id}/content`;
  assert.equal((await request(b, content, { token: operator })).status, 409);
  assert.equal(
    (await request(a, content, { token: operator, method: "PUT", body: bytes }))
      .status,
    200,
  );
  assert.deepEqual(
    (await request(b, content, { token: operator })).bytes,
    bytes,
  );
  pass(
    "Contiguous SSE, actual sandbox file write/read and TLS S3 artifact publication/download",
  );
  await post(a, `/sessions/${session}/turns`, {
    submission_id: randomUUID(),
    text: plan("run_shell", {
      command:
        "test ! -S /var/run/docker.sock && test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token && ! cat /opt/probe-denied/sentinel && grep -E 'NoNewPrivs|Seccomp:' /proc/self/status",
    }),
  });
  await approve(await state("waiting"));
  const guarded = await state("completed");
  const guardLog = await events(guarded.cursor),
    guard = guardLog
      .filter((e) => e.type === "tool_finished" && e.data.name === "run_shell")
      .at(-1).data.result;
  const guardText = JSON.stringify(guard);
  assert.match(guardText, /Permission denied/);
  assert.match(guardText, /NoNewPrivs/);
  pass(
    "Real sandbox denies the out-of-project sentinel and has no Docker socket or Kubernetes service-account token",
  );
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "restart",
    "deployment/ow-openworker-api",
  ]);
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "status",
    "deployment/ow-openworker-api",
    "--timeout=120s",
  ]);
  const fresh = await pods("api");
  a = await forward(fresh[0].metadata.name);
  b = await forward(fresh[1].metadata.name);
  assert.deepEqual(
    (await request(a, content, { token: operator })).bytes,
    bytes,
  );
  assert.equal((await snap()).session.id, session);
  const client = spawn(
    process.execPath,
    [join(repo, "harness/server-reference/client.mjs"), "--session", session],
    {
      env: {
        PATH: process.env.PATH,
        NODE_EXTRA_CA_CERTS: join(root, "private/api.ca.crt"),
        NODE_OPTIONS: "--dns-result-order=ipv4first",
        OW_API_URLS: `https://localhost:${a},https://localhost:${b}`,
        OW_API_TOKEN_FILE: join(root, "private/admin"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let output = "";
  client.stdout.on("data", (b) => (output += b));
  client.stderr.resume();
  const timer = setTimeout(() => client.kill("SIGTERM"), 20000);
  try {
    const [code] = await once(client, "exit");
    assert.equal(code, 0);
    assert.match(output, /completed/);
  } finally {
    clearTimeout(timer);
  }
  pass(
    "Shipped Node client reconnects through TLS using the explicit local CA",
  );
  await post(a, `/credentials/${credential.id}/revoke`, {}, admin);
  assert.equal(
    (await request(b, `/sessions/${session}`, { token: operator })).status,
    401,
  );
  pass(
    "Fresh API pods restore results and enforce shared credential revocation",
  );
  await writeFile(
    join(root, "qualification.json"),
    JSON.stringify(
      {
        session,
        environment: env.id,
        artifact: artifact.id,
        sha256: hash,
        checks,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
} finally {
  for (const child of forwards)
    if (child.exitCode === null) child.kill("SIGTERM");
}
