// Prepare public installer inputs; stage private fixture credentials only on request.
import { readFile, writeFile, mkdir, chmod } from "node:fs/promises";
import { resolve, join } from "node:path";
import { randomUUID } from "node:crypto";
import { serviceConfig, run } from "./stack.mjs";
import { localValues } from "./local-values.mjs";
const [action, rootArg, mode = "install"] = process.argv.slice(2),
  root = resolve(rootArg || "");
if (!rootArg || !["prepare", "fixtures"].includes(action))
  throw Error(
    "Usage: prepare-local-install.mjs prepare ROOT install|existing; fixtures ROOT",
  );
if (action === "prepare") {
  if (!["install", "existing"].includes(mode))
    throw Error("Invalid gateway mode");
  await mkdir(root, { mode: 0o700 });
  const id = randomUUID().replaceAll("-", "").slice(0, 12),
    short = id.slice(0, 8),
    serviceId = "ows-" + id;
  const base = JSON.parse(
    await readFile(".ow-service-build/m5-final-install.json", "utf8"),
  );
  const namespace =
    "ow-service-m4-" + (mode === "install" ? "own" : "reuse") + short;
  const shellNs =
      mode === "install" ? "ow-local-shell-" + short : "ow-helm-shell",
    fullname = mode === "install" ? "ow-local-gw-" + short : "ow-helm-gateway";
  const connection = {
    serviceId,
    scripted: true,
    runner: resolve(".ow-service-build/out/openworker"),
    openshell: {
      endpoint: `https://${fullname}.${shellNs}.svc.cluster.local:8080`,
      tls_server_name: `${fullname}.${shellNs}.svc.cluster.local`,
      certificates: join(root, "gateway"),
      workspace: "default",
      image: base.runnerImage,
    },
  };
  const values = localValues(serviceConfig(connection, serviceId), base);
  await writeFile(
    join(root, "service-values.json"),
    JSON.stringify(values, null, 2),
    { mode: 0o600, flag: "wx" },
  );
  const shell = { mode, namespace: shellNs, release: "ow-shell", fullname };
  if (mode === "install") {
    shell.archive = resolve(
      ".ow-service-local-install/cache/helm-chart-0.1.3.tgz",
    );
    shell.valuesFile = join(root, "openshell-values.json");
    const pin = (digest) => ({ digest: "sha256:" + digest });
    await writeFile(
      shell.valuesFile,
      JSON.stringify(
        {
          fullnameOverride: fullname,
          global: { image: { tag: "0.1.3" } },
          gateway: {
            image: pin(
              "ea14aa4db0980fab4769fb6b5509ac42ae3dc8495e645b1ba1fe1ada49892fa6",
            ),
          },
          sandboxRuntime: {
            image: pin(
              "2658534bfb3f1ebba4bb7792e9e875a0972edbe7ae5f58d807ae22c46844a2ac",
            ),
          },
          supervisor: {
            image: pin(
              "4ab2b970cdb02c7518ba4ccbbadb4861ae9a8d0b8770e5b160697f0453b0a8e4",
            ),
          },
          sandbox: {
            image: {
              repository: base.runnerImage.split("@")[0],
              digest: base.runnerImage.split("@")[1],
            },
          },
          server: {
            telemetryEnabled: false,
            auth: { allowUnauthenticatedUsers: false },
            workspaceDefaultStorageSize: "1Gi",
          },
          resources: {
            requests: { cpu: "100m", memory: "128Mi" },
            limits: { cpu: "1", memory: "512Mi" },
          },
        },
        null,
        2,
      ),
      { mode: 0o600, flag: "wx" },
    );
  }
  const config = {
    schemaVersion: 1,
    kubeconfig: base.kubeconfig,
    context: base.context,
    helm: "/Users/rohit/fleet/ro4d/.harness-server-tools-20261011.BHeymf/darwin-arm64/helm",
    service: {
      namespace,
      release: "ows",
      chart: resolve("deploy/service/chart"),
      valuesFile: join(root, "service-values.json"),
      fixtureDatabase: true,
    },
    openshell: shell,
  };
  await writeFile(
    join(root, "installer-config.json"),
    JSON.stringify(config, null, 2),
    { mode: 0o600, flag: "wx" },
  );
  await writeFile(
    join(root, "connection.json"),
    JSON.stringify(connection, null, 2),
    { mode: 0o600, flag: "wx" },
  );
  await writeFile(
    join(root, "fixture-config.json"),
    JSON.stringify(
      {
        ...base,
        namespace,
        connectionFile: join(root, "connection.json"),
        helm: config.helm,
      },
      null,
      2,
    ),
    { mode: 0o600, flag: "wx" },
  );
  console.log(
    JSON.stringify({
      root,
      mode,
      namespace,
      shellNamespace: shellNs,
      serviceId,
    }),
  );
} else {
  const c = JSON.parse(
    await readFile(join(root, "installer-config.json"), "utf8"),
  );
  // Save key material privately; never log the Secret body.
  const raw = JSON.parse(
    await run("kubectl", [
      "--kubeconfig",
      c.kubeconfig,
      "--context",
      c.context,
      "-n",
      c.openshell.namespace,
      "get",
      "secret",
      "openshell-client-tls",
      "-o",
      "json",
    ]),
  );
  await mkdir(join(root, "gateway"), { mode: 0o700 });
  for (const name of ["ca.crt", "tls.crt", "tls.key"]) {
    if (!raw.data?.[name]) throw Error("Gateway TLS material missing");
    await writeFile(
      join(root, "gateway", name),
      Buffer.from(raw.data[name], "base64"),
      { mode: 0o600, flag: "wx" },
    );
  }
  const fixture = join(root, "fixture");
  await run("node", [
    resolve("deploy/service/kubernetes-local.mjs"),
    "init",
    fixture,
    join(root, "fixture-config.json"),
  ]);
  await run("node", [
    resolve("deploy/service/kubernetes-local.mjs"),
    "fixtures",
    fixture,
  ]);
  const actual = JSON.parse(
      await readFile(join(fixture, "values.json"), "utf8"),
    ),
    planned = JSON.parse(
      await readFile(join(root, "service-values.json"), "utf8"),
    );
  if (JSON.stringify(actual) !== JSON.stringify(planned))
    throw Error("Fixture values differ from reviewed installer input");
  console.log(
    "Private PostgreSQL/TLS fixtures prepared; reviewed chart values unchanged.",
  );
}
