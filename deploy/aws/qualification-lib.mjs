// Bounded HTTP/Kubernetes helpers for the explicitly owned AWS experiment.
// No browser automation, model credential access or automatic mutation retries.
import assert from "node:assert/strict";
import https from "node:https";
import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { repo, kubectl, apply } from "../helm/local.mjs";
import { name } from "./templates.mjs";
import { account } from "./client.mjs";
export const root = join(repo, ".ow-aws");
// Qualification-specific filters, not replacements for server-side admission.
export const deploymentPods = (items) =>
  items.filter((p) =>
    p.metadata.ownerReferences?.some((r) => r.kind === "ReplicaSet"),
  );
export const hasFixtureWorker = (pool) =>
  pool.workers.some(
    (w) =>
      !w.draining &&
      w.selector.scripted === true &&
      w.selector.fallback_model === "fixture",
  );
export function postgresEnvironment(connection) {
  const u = new URL(connection);
  assert.equal(u.protocol, "postgresql:");
  assert.equal(u.pathname, "/openworker");
  assert.ok(u.hostname && u.username && u.password && !u.search && !u.hash);
  return {
    PGHOST: u.hostname,
    PGPORT: u.port || "5432",
    PGDATABASE: "openworker",
    PGUSER: decodeURIComponent(u.username),
    PGPASSWORD: decodeURIComponent(u.password),
    PGCONNECT_TIMEOUT: "10",
  };
}
export async function context() {
  const m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
  const plan = JSON.parse(await readFile(join(root, "plan.json"), "utf8"));
  assert.equal(m.context, name);
  assert.equal(plan.name, name);
  assert.equal(plan.account, account);
  assert.ok(Date.now() < Date.parse(plan.expiry));
  const ns = JSON.parse(
    await kubectl(m, ["get", "namespace", "ow-app", "-o", "json"]),
  );
  assert.equal(ns.metadata.labels["openworker.dev/experiment"], name);
  return m;
}
export const save = (file, data) =>
  writeFile(join(root, file), JSON.stringify(data, null, 2), { mode: 0o600 });
export async function wait(fn, ms = 120000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const result = await fn();
    if (result) return result;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw Error("Qualification state deadline");
}
export async function pods(m, role, release = "ow") {
  return JSON.parse(
    await kubectl(m, [
      "-n",
      "ow-app",
      "get",
      "pods",
      "-l",
      `app.kubernetes.io/instance=${release},app.kubernetes.io/component=${role}`,
      "-o",
      "json",
    ]),
  ).items.filter(
    (p) =>
      !p.metadata.deletionTimestamp &&
      p.status.containerStatuses?.every((c) => c.ready),
  );
}
export async function forward(m, pod, remote = 8443) {
  const child = spawn(
    "kubectl",
    [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "-n",
      "ow-app",
      "port-forward",
      "--address",
      "127.0.0.1",
      "pod/" + pod,
      `0:${remote}`,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  child.stderr.resume();
  const port = await new Promise((resolve, reject) => {
    let out = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(Error("Forward deadline"));
    }, 20000);
    child.on("error", reject);
    child.on("exit", () => {
      clearTimeout(timer);
      reject(Error("Forward ended"));
    });
    child.stdout.on("data", (b) => {
      out += b;
      const match = out.match(/127\.0\.0\.1:(\d+) ->/);
      if (match) {
        clearTimeout(timer);
        resolve(Number(match[1]));
      }
    });
  });
  return { port, close: () => child.kill("SIGTERM") };
}
export async function client(port, key = "operator") {
  const token = (await readFile(join(root, key), "utf8")).trim(),
    ca = await readFile(join(root, "ca.crt"));
  async function request(path, { body, raw, method, stream = false } = {}) {
    return new Promise((resolve, reject) => {
      const bytes =
        raw ??
        (body === undefined ? undefined : Buffer.from(JSON.stringify(body)));
      const r = https.request(
        {
          hostname: "127.0.0.1",
          port,
          servername: "localhost",
          ca,
          path,
          method: method || (body ? "POST" : "GET"),
          headers: {
            Authorization: "Bearer " + token,
            ...(bytes
              ? {
                  "Content-Type": raw
                    ? "application/octet-stream"
                    : "application/json",
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
          let size = 0;
          res.on("data", (b) => {
            size += b.length;
            if (size > 4 * 1024 * 1024) {
              res.destroy(Error("Bounded response exceeded"));
              return;
            }
            chunks.push(b);
          });
          res.on("error", reject);
          res.on("end", () =>
            resolve({ status: res.statusCode, bytes: Buffer.concat(chunks) }),
          );
        },
      );
      r.on("error", reject);
      r.setTimeout(30000, () => r.destroy(Error("HTTP deadline")));
      r.end(bytes);
    });
  }
  async function json(path, body) {
    const r = await request(path, { body });
    assert.ok(r.status >= 200 && r.status < 300, `${path}: ${r.status}`);
    return JSON.parse(r.bytes);
  }
  const snapshot = (sid) => json("/sessions/" + sid);
  async function state(sid, status) {
    return wait(async () => {
      const s = await snapshot(sid);
      assert.ok(
        !["failed", "truncated"].includes(s.turn?.status),
        `unexpected ${s.turn?.status}`,
      );
      return s.turn?.status === status && s;
    });
  }
  async function events(sid, through) {
    const res = await request(`/sessions/${sid}/events?after=0`, {
      stream: true,
    });
    assert.equal(res.statusCode, 200);
    const out = [];
    let buffer = "";
    const timer = setTimeout(
      () => res.destroy(Error("Replay deadline")),
      30000,
    );
    try {
      for await (const chunk of res) {
        buffer += chunk.toString();
        let i;
        while ((i = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, i);
          buffer = buffer.slice(i + 2);
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const e = JSON.parse(line.slice(6));
          assert.equal(e.event_id, out.length + 1);
          out.push(e);
          if (e.event_id >= through) return out;
        }
      }
      throw Error("Replay incomplete");
    } finally {
      clearTimeout(timer);
      res.destroy();
    }
  }
  async function approve(sid, s, expectedName, response = "once") {
    const i = s.interactions.find((i) => !i.response);
    assert.ok(i);
    assert.equal(i.kind, "approval");
    assert.equal(i.call.name, expectedName);
    return json(`/sessions/${sid}/controls`, {
      request_id: randomUUID(),
      turn_id: i.turn_id,
      action: {
        kind: "response",
        interaction_id: i.id,
        response,
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
  return { request, json, snapshot, state, events, approve };
}
export async function admin(m, args, release = "ow") {
  const dep = JSON.parse(
    await kubectl(m, [
      "-n",
      "ow-app",
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
  c.name = "admin";
  delete c.envFrom;
  delete c.readinessProbe;
  delete c.livenessProbe;
  c.env = c.env.filter((e) => e.name !== "POD_NAME");
  c.volumeMounts = c.volumeMounts.filter((v) => v.name !== "openshell");
  spec.volumes = spec.volumes.filter((v) => v.name !== "openshell");
  spec.restartPolicy = "Never";
  const id = "ow-current-" + randomUUID().slice(0, 8);
  await apply(m, [
    {
      apiVersion: "batch/v1",
      kind: "Job",
      metadata: {
        name: id,
        namespace: "ow-app",
        labels: { "openworker.dev/experiment": name },
      },
      spec: {
        backoffLimit: 0,
        template: {
          metadata: { labels: dep.spec.template.metadata.labels },
          spec,
        },
      },
    },
  ]);
  await kubectl(m, [
    "-n",
    "ow-app",
    "wait",
    "--for=condition=complete",
    "job/" + id,
    "--timeout=120s",
  ]);
  return kubectl(m, ["-n", "ow-app", "logs", "job/" + id, "-c", "admin"]);
}
export const planTool = (name, args) =>
  "TEST_RESOURCE_GRANTS " + JSON.stringify([{ name, arguments: args }]);
export async function permitPodIdentity(m, release) {
  assert.ok(["variant", "restore", "live"].includes(release));
  const p = JSON.parse(
    await kubectl(m, [
      "-n",
      "ow-app",
      "get",
      "networkpolicy",
      "api-pod-identity",
      "-o",
      "json",
    ]),
  );
  p.metadata = {
    name: release + "-pod-identity",
    namespace: "ow-app",
    labels: { "openworker.dev/experiment": name },
  };
  p.spec.podSelector.matchLabels["app.kubernetes.io/instance"] = release;
  await apply(m, [p]);
}
