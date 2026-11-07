// Qualification-only localhost collector sidecars; no public telemetry endpoint.
// Customer production collectors/retention remain deployment-owned.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { kubectl, apply } from "../helm/local.mjs";
import {
  root,
  context,
  save,
  wait,
  pods,
  forward,
  client,
  planTool,
} from "./qualification-lib.mjs";
const m = await context(),
  k = (args) => kubectl(m, ["-n", "ow-app", ...args]);
const image =
  "otel/opentelemetry-collector-contrib@sha256:e7c92c715f28ff142f3bcaccd4fc5603cf4c71276ef09954a38eb4038500a5a5";
const collector = {
  receivers: { otlp: { protocols: { http: { endpoint: "127.0.0.1:4318" } } } },
  processors: {
    memory_limiter: {
      check_interval: "1s",
      limit_mib: 100,
      spike_limit_mib: 20,
    },
    batch: { timeout: "1s" },
  },
  exporters: { debug: { verbosity: "detailed" } },
  service: {
    telemetry: { metrics: { level: "none" } },
    pipelines: Object.fromEntries(
      ["traces", "metrics", "logs"].map((signal) => [
        signal,
        {
          receivers: ["otlp"],
          processors: ["memory_limiter", "batch"],
          exporters: ["debug"],
        },
      ]),
    ),
  },
};
await apply(m, [
  {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: {
      name: "ow-qualification-collector",
      namespace: "ow-app",
      labels: { "openworker.dev/experiment": m.context },
    },
    immutable: true,
    data: { "collector.json": JSON.stringify(collector) },
  },
]);
const cm = JSON.parse(
  await k(["get", "configmap", "ow-openworker", "-o", "json"]),
);
const config = JSON.parse(cm.data["reference.json"]);
config.telemetry = { endpoint: "http://127.0.0.1:4318", deployment: m.context };
cm.data["reference.json"] = JSON.stringify(config);
delete cm.metadata.managedFields;
await apply(m, [cm]);
for (const role of ["api", "worker"]) {
  const dep = JSON.parse(
    await k(["get", "deployment", "ow-openworker-" + role, "-o", "json"]),
  );
  assert.ok(
    !dep.spec.template.spec.containers.some((c) => c.name === "collector"),
    "Do not rerun telemetry installation blindly",
  );
  const patch = {
    spec: {
      template: {
        metadata: {
          annotations: {
            "qualification.openworker.dev/telemetry": randomUUID(),
          },
        },
        spec: {
          containers: [
            {
              name: "collector",
              image,
              args: ["--config=/collector/collector.json"],
              securityContext: {
                runAsNonRoot: true,
                runAsUser: 1000,
                allowPrivilegeEscalation: false,
                readOnlyRootFilesystem: true,
                capabilities: { drop: ["ALL"] },
                seccompProfile: { type: "RuntimeDefault" },
              },
              resources: {
                requests: { cpu: "30m", memory: "64Mi" },
                limits: { cpu: "250m", memory: "160Mi" },
              },
              volumeMounts: [
                { name: "collector", mountPath: "/collector", readOnly: true },
              ],
            },
          ],
          volumes: [
            {
              name: "collector",
              configMap: { name: "ow-qualification-collector" },
            },
          ],
        },
      },
    },
  };
  await k([
    "patch",
    "deployment",
    "ow-openworker-" + role,
    "--type=strategic",
    "-p",
    JSON.stringify(patch),
  ]);
  await k([
    "rollout",
    "status",
    "deployment/ow-openworker-" + role,
    "--timeout=180s",
  ]);
}
const f = await forward(m, (await pods(m, "api"))[0].metadata.name);
try {
  const a = await client(f.port),
    environment = JSON.parse(
      await readFile(join(root, "environment.json"), "utf8"),
    );
  const canary = "PRIVATE_CONTENT_" + randomUUID();
  const sid = (
    await a.json("/sessions", {
      request_id: randomUUID(),
      parameters: {
        name: canary,
        coworker: "task-desk",
        workspace: "/sandbox",
        environment_id: environment.id,
      },
    })
  ).session_id;
  await a.json(`/sessions/${sid}/turns`, {
    submission_id: randomUUID(),
    text: planTool("write_file", {
      path: "telemetry-" + randomUUID() + ".txt",
      content: canary,
    }),
  });
  await a.approve(sid, await a.state(sid, "waiting"), "write_file");
  await a.state(sid, "completed");
  const logs = await wait(async () => {
    const ps = [...(await pods(m, "api")), ...(await pods(m, "worker"))];
    const logs = (
      await Promise.all(
        ps.map((p) => k(["logs", p.metadata.name, "-c", "collector"])),
      )
    ).join("\n");
    return (
      ["ResourceSpans", "ResourceMetrics", "ResourceLog"].every((s) =>
        logs.includes(s),
      ) &&
      logs.includes("openworker") &&
      logs
    );
  }, 150000);
  assert.ok(
    !logs.includes(canary),
    "Private prompt/file content reached collector",
  );
  assert.ok(!logs.includes(sid), "Raw session identifier reached collector");
  await save("collector-evidence.json", { logs });
  await save("current-telemetry-qualification.json", {
    status: "completed",
    time: new Date().toISOString(),
    session: sid,
    collector: image,
    checks: [
      "Real AWS API/worker OTLP traces, metrics and lifecycle logs accepted by pinned collector",
      "Unique task/file-content canary and raw session ID absent from collected output",
    ],
    limits: [
      "Collector debug output retained privately; no production Grafana or remote telemetry retention deployed",
      "Absence check is for this fixture, not a general proof of all possible sensitive data",
    ],
  });
  console.log(
    "PASS AWS collector receives traces, metrics and logs; fixture content remains absent",
  );
} finally {
  f.close();
}
