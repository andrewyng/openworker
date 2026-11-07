// Isolated OpenShell admin CLI profile for inspection of this experiment only.
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { repo, kubectl } from "../helm/local.mjs";
import { name } from "./templates.mjs";
const root = join(repo, ".ow-aws"),
  m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
if (m.context !== name) throw Error("Wrong cluster");
const tls = join(root, "openshell-tls");
await mkdir(tls, { recursive: true, mode: 0o700 });
await mkdir(join(tls, "client"), { recursive: true, mode: 0o700 });
const secret = JSON.parse(
  await kubectl(m, [
    "-n",
    "ow-shell",
    "get",
    "secret",
    "openshell-client-tls",
    "-o",
    "json",
  ]),
);
for (const [key, file] of [
  ["ca.crt", "ca.crt"],
  ["tls.crt", "client/tls.crt"],
  ["tls.key", "client/tls.key"],
])
  await writeFile(join(tls, file), Buffer.from(secret.data[key], "base64"), {
    mode: 0o600,
  });
const cli = process.env.OW_OPENSHELL;
if (!cli) throw Error("Set OW_OPENSHELL to the pinned 0.1.3 CLI");
const env = {
  ...process.env,
  XDG_CONFIG_HOME: join(root, "openshell-config"),
  XDG_STATE_HOME: join(root, "openshell-state"),
  OPENSHELL_SYSTEM_GATEWAY_DIR: join(root, "openshell-system"),
  OPENSHELL_LOCAL_TLS_DIR: tls,
};
const args = process.argv.slice(2);
if (args[0] === "record-absence") {
  const original = JSON.parse(
    await kubectl(m, [
      "-n",
      "ow-app",
      "logs",
      "job/ow-environment-receipt",
      "-c",
      "admin",
    ]),
  );
  const remote = "owr-" + original.id.slice(4, 19);
  let absent = false;
  try {
    await promisify(execFile)(
      cli,
      ["sandbox", "get", remote, "--gateway", name, "--workspace", "default"],
      { env, timeout: 30000 },
    );
  } catch (e) {
    absent =
      String(e.stderr).includes("sandbox not found") &&
      String(e.stderr).includes("Some requested entity was not found");
    if (!absent) throw Error("Gateway absence not established");
  }
  if (!absent) throw Error("Original sandbox still exists");
  const objects = JSON.parse(
    await kubectl(m, ["-n", "ow-shell", "get", "sandboxes,pvc", "-o", "json"]),
  );
  if (objects.items.some((x) => x.metadata.name.includes(remote)))
    throw Error("Original Kubernetes resource remains");
  await writeFile(
    join(root, "absent-original.json"),
    JSON.stringify(
      {
        observedAt: new Date().toISOString(),
        environment_id: original.id,
        remote_name: remote,
        providerNotFound: true,
        kubernetesObjectsAbsent: true,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "Authenticated original sandbox absence and Kubernetes resource absence recorded; failed receipt retained.",
  );
  process.exit(0);
}
if (args[0] === "register")
  args.splice(
    0,
    args.length,
    "gateway",
    "add",
    "--local",
    "--name",
    name,
    "https://localhost:9445",
  );
else args.push("--gateway", name, "--workspace", "default");
const { stdout } = await promisify(execFile)(cli, args, {
  env,
  timeout: 120000,
  maxBuffer: 1024 * 1024,
});
console.log(stdout);
