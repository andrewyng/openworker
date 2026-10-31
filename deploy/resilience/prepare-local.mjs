// Fresh owned local service with recording enabled; OpenShell already installed.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { run } from "../service/stack.mjs";
const [directory, configFile] = process.argv.slice(2);
assert(
  directory && configFile,
  "Usage: prepare-local.mjs NEW_IGNORED_ROOT LOCAL_CONFIG.json",
);
const root = resolve(directory),
  c = JSON.parse(await readFile(resolve(configFile)));
assert.match(c.context, /^kind-[a-z0-9-]+$/);
assert.match(c.namespace, /^ow-service-m4-resilience[a-z0-9]+$/);
assert.match(c.imageRef, /@sha256:[a-f0-9]{64}$/);
assert.match(c.runnerImage, /@sha256:[a-f0-9]{64}$/);
assert.equal(
  (await run("git", ["check-ignore", join(root, "private")])).trim(),
  join(root, "private"),
);
// kind imports local tags, while the chart correctly requires immutable digests.
// Register aliases only after checking the imported content and cluster labels.
const nodes = JSON.parse(
  await run("kubectl", [
    "--kubeconfig",
    c.kubeconfig,
    "--context",
    c.context,
    "get",
    "nodes",
    "-o",
    "json",
  ]),
).items;
assert(nodes.length > 0);
for (const node of nodes) {
  const name = node.metadata.name;
  const container = JSON.parse(await run("docker", ["inspect", name]))[0];
  assert.equal(
    container.Config.Labels["io.x-k8s.kind.cluster"],
    c.context.slice(5),
  );
  const rows = (
    await run("docker", ["exec", name, "ctr", "-n", "k8s.io", "images", "ls"])
  )
    .trim()
    .split("\n")
    .slice(1)
    .map((s) => s.trim().split(/\s+/));
  for (const ref of [c.imageRef, c.runnerImage]) {
    const digest = ref.split("@")[1],
      existing = rows.find((r) => r[0] === ref);
    if (existing) {
      assert.equal(existing[2], digest);
      continue;
    }
    const imported = rows.find((r) => r[2] === digest);
    assert(
      imported,
      "Load the pinned image into this kind node before preparation",
    );
    await run("docker", [
      "exec",
      name,
      "ctr",
      "-n",
      "k8s.io",
      "images",
      "tag",
      imported[0],
      ref,
    ]);
  }
}
await mkdir(root, { mode: 0o700 });
const connection = {
  ...c.connection,
  scripted: true,
  observability: {
    schema: "ow_observability",
    queue_capacity: 2048,
    retention_days: 1,
  },
};
delete connection.modelKeyFile;
const connectionFile = join(root, "connection.json");
await writeFile(connectionFile, JSON.stringify(connection), {
  mode: 0o600,
  flag: "wx",
});
const k = { ...c, connectionFile };
delete k.connection;
const local = join(root, "install.json");
await writeFile(local, JSON.stringify(k), { mode: 0o600, flag: "wx" });
// The existing installer remains
// the owner of TLS/bootstrap/namespace checks and Helm installation.
await run("node", [
  "deploy/service/kubernetes-local.mjs",
  "init",
  join(root, "service"),
  local,
]);
const input = join(root, "service/input/config.json");
const config = JSON.parse(await readFile(input));
config.telemetry = {
  endpoint: "http://127.0.0.1:4318",
  deployment: "resilience",
  signals: ["traces"],
};
// Each worker will forward its loopback collector port using a sidecar. There
// are no tokens in OTLP and no relaxation of the production HTTPS requirements.
await writeFile(input, JSON.stringify(config, null, 2), { mode: 0o600 });
await run(
  "node",
  ["deploy/service/kubernetes-local.mjs", "install", join(root, "service")],
  { inherit: true },
);
console.log(
  JSON.stringify({
    state: join(root, "service"),
    serviceId: JSON.parse(await readFile(join(root, "service/kubernetes.json")))
      .id,
  }),
);
