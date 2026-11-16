// Actual deny/allow qualification on the exact disposable EKS cluster.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { name } from "./templates.mjs";
import { repo, kubectl, apply } from "../helm/local.mjs";
const m = JSON.parse(
  await readFile(join(repo, ".ow-aws/kubernetes.json"), "utf8"),
);
if (m.context !== name) throw Error("Unexpected cluster");
const namespace = "ow-network-proof",
  image =
    "busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e";
const namespaces = JSON.parse(await kubectl(m, ["get", "ns", "-o", "json"]));
if (namespaces.items.some((n) => n.metadata.name === namespace))
  throw Error("Previous network proof exists; inspect it first");
await apply(m, [
  {
    apiVersion: "v1",
    kind: "Namespace",
    metadata: {
      name: namespace,
      labels: { "openworker.dev/experiment": name },
    },
  },
]);
const checks = [];
let client, server;
const policy = (role, direction, allowed) => ({
  apiVersion: "networking.k8s.io/v1",
  kind: "NetworkPolicy",
  metadata: { name: role + "-" + direction.toLowerCase(), namespace },
  spec: {
    podSelector: { matchLabels: { role } },
    policyTypes: [direction],
    [direction === "Ingress" ? "ingress" : "egress"]: allowed
      ? [
          {
            [direction === "Ingress" ? "from" : "to"]: [
              {
                podSelector: {
                  matchLabels: {
                    role: role === "client" ? "server" : "client",
                  },
                },
              },
            ],
            ports: [{ protocol: "TCP", port: 8080 }],
          },
        ]
      : [],
  },
});
async function observe(label, expected) {
  let matches = 0;
  for (let n = 0; n < 12; n++) {
    const out = await kubectl(m, [
      "-n",
      namespace,
      "exec",
      client,
      "--",
      "sh",
      "-c",
      `wget -q -T 2 -O - http://${server}:8080 || printf denied`,
    ]);
    const good = expected
      ? out.trim() === "network-proof"
      : out.trim() === "denied";
    matches = good ? matches + 1 : 0;
    if (matches === 3) {
      checks.push(label);
      console.log("PASS " + label);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw Error("Policy did not enforce " + label);
}
try {
  await apply(m, [
    policy("client", "Egress", true),
    policy("server", "Ingress", true),
  ]);
  for (const role of ["client", "server"])
    await apply(m, [
      {
        apiVersion: "apps/v1",
        kind: "Deployment",
        metadata: { name: role, namespace },
        spec: {
          replicas: 1,
          selector: { matchLabels: { role } },
          template: {
            metadata: { labels: { role } },
            spec: {
              automountServiceAccountToken: false,
              containers: [
                {
                  name: role,
                  image,
                  command:
                    role === "client"
                      ? ["sleep", "3600"]
                      : [
                          "sh",
                          "-c",
                          "mkdir -p /tmp/www; printf network-proof > /tmp/www/index.html; exec httpd -f -p 8080 -h /tmp/www",
                        ],
                  resources: {
                    requests: { cpu: "10m", memory: "8Mi" },
                    limits: { cpu: "100m", memory: "32Mi" },
                  },
                  securityContext: {
                    allowPrivilegeEscalation: false,
                    capabilities: { drop: ["ALL"] },
                    seccompProfile: { type: "RuntimeDefault" },
                  },
                },
              ],
            },
          },
        },
      },
    ]);
  for (const role of ["client", "server"])
    await kubectl(m, [
      "-n",
      namespace,
      "rollout",
      "status",
      "deployment/" + role,
      "--timeout=120s",
    ]);
  const pods = JSON.parse(
    await kubectl(m, ["-n", namespace, "get", "pods", "-o", "json"]),
  ).items;
  client = pods.find((p) => p.metadata.labels.role === "client").metadata.name;
  server = pods.find((p) => p.metadata.labels.role === "server").status.podIP;
  await observe("explicit baseline allow", true);
  await apply(m, [policy("client", "Egress", false)]);
  await observe("egress deny", false);
  await apply(m, [policy("client", "Egress", true)]);
  await observe("egress allow", true);
  await apply(m, [policy("server", "Ingress", false)]);
  await observe("ingress deny", false);
  await apply(m, [policy("server", "Ingress", true)]);
  await observe("ingress allow", true);
  assert.equal(checks.length, 5);
  await writeFile(
    join(repo, ".ow-aws/network-proof.json"),
    JSON.stringify({ time: new Date().toISOString(), checks }, null, 2),
    { mode: 0o600 },
  );
} finally {
  if (checks.length === 5)
    await kubectl(m, ["delete", "namespace", namespace, "--wait=false"]);
  else
    console.error(
      "Failed probe namespace retained for diagnosis: " + namespace,
    );
}
