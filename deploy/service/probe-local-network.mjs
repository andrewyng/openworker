// Functional CNI probe in an existing installer-owned test namespace only.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { run } from "./stack.mjs";
const root = resolve(process.argv[2] || "");
const plan = JSON.parse(
  await readFile(join(root, "state/local-plan.json"), "utf8"),
);
const m = JSON.parse(
  await readFile(join(root, "fixture/kubernetes.json"), "utf8"),
);
const kube = (args, input) =>
  run(
    "kubectl",
    [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "-n",
      m.namespace,
      ...args,
    ],
    { input },
  );
assert.ok(m.context.startsWith("kind-"));
assert.equal(plan.config.service.namespace, m.namespace);
const namespace = JSON.parse(
  await kube(["get", "namespace", m.namespace, "-o", "json"]),
);
assert.equal(namespace.metadata.labels["openworker.io/installer-id"], plan.id);
const image = JSON.parse(
  await readFile(join(root, "service-values.json"), "utf8"),
).image;
const nginx =
  "nginx@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8";
const label = "openworker.io/network-probe";
const policy = (role, direction, allow) => ({
  apiVersion: "networking.k8s.io/v1",
  kind: "NetworkPolicy",
  metadata: { name: "installer-" + role + "-policy" },
  spec: {
    podSelector: { matchLabels: { [label]: role } },
    policyTypes: [direction],
    [direction.toLowerCase()]: allow
      ? [
          {
            [direction === "Ingress" ? "from" : "to"]: [
              {
                podSelector: {
                  matchLabels: {
                    [label]: role === "client" ? "server" : "client",
                  },
                },
              },
            ],
            ports: [{ protocol: "TCP", port: 80 }],
          },
        ]
      : [],
  },
});
const apply = (object) => kube(["apply", "-f", "-"], JSON.stringify(object));
const checks = [];
try {
  for (const role of ["client", "server"])
    await kube(
      ["create", "-f", "-"],
      JSON.stringify({
        apiVersion: "v1",
        kind: "Pod",
        metadata: {
          name: "installer-network-" + role,
          labels: { [label]: role },
        },
        spec: {
          automountServiceAccountToken: false,
          containers: [
            {
              name: role,
              image: role === "client" ? image : nginx,
              imagePullPolicy: "IfNotPresent",
              ...(role === "client"
                ? { command: ["sleep", "600"] }
                : { readinessProbe: { httpGet: { path: "/", port: 80 } } }),
              resources: {
                requests: { cpu: "10m", memory: "16Mi" },
                limits: { cpu: "100m", memory: "96Mi" },
              },
            },
          ],
          restartPolicy: "Never",
        },
      }),
    );
  await apply(policy("client", "Egress", true));
  await apply(policy("server", "Ingress", true));
  await kube([
    "wait",
    "--for=condition=Ready",
    "pod",
    "-l",
    label,
    "--timeout=90s",
  ]);
  const server = JSON.parse(
    await kube(["get", "pod", "installer-network-server", "-o", "json"]),
  ).status.podIP;
  async function observe(name, allowed) {
    let matches = 0;
    for (let n = 0; n < 12; n++) {
      const out = await kube([
        "exec",
        "installer-network-client",
        "--",
        "sh",
        "-c",
        `curl --noproxy '*' -sS --connect-timeout 1 --max-time 2 http://${server}/ 2>/dev/null || printf denied`,
      ]);
      const good = allowed
        ? out.includes("Welcome to nginx!")
        : out === "denied";
      matches = good ? matches + 1 : 0;
      if (matches === 3) {
        checks.push(name);
        return;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw Error("Network enforcement probe failed: " + name);
  }
  await observe("explicit baseline allow", true);
  await apply(policy("client", "Egress", false));
  await observe("egress deny", false);
  await apply(policy("client", "Egress", true));
  await observe("egress allow", true);
  await apply(policy("server", "Ingress", false));
  await observe("ingress deny", false);
  await apply(policy("server", "Ingress", true));
  await observe("ingress allow", true);
  const report = {
    passed: true,
    checks,
    namespace: m.namespace,
    cloudCalls: false,
  };
  await writeFile(
    join(root, "fixture/qualification-network.json"),
    JSON.stringify(report, null, 2),
    { mode: 0o600 },
  );
  console.log(JSON.stringify(report));
} finally {
  if (checks.length === 5) {
    await kube([
      "delete",
      "pod",
      "installer-network-client",
      "installer-network-server",
      "--wait=true",
      "--timeout=60s",
    ]);
    await kube([
      "delete",
      "networkpolicy",
      "installer-client-policy",
      "installer-server-policy",
    ]);
  } else
    console.error(
      "Failed probe resources retained in the owned test namespace for inspection.",
    );
}
