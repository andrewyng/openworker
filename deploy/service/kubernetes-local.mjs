// Local kind installer. No default kubecontext changes, cloud resources, or registry pushes.
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { randomBytes } from "node:crypto";
import { init, run } from "./stack.mjs";
import { localValues } from "./local-values.mjs";
const [action, root, configFile] = process.argv.slice(2);
if (!action || !root)
  throw Error(
    "Usage: node kubernetes-local.mjs init STATE CONFIG.json | install STATE | status STATE",
  );
const state = resolve(root);
const kube = (m, args, input) =>
  run(
    "kubectl",
    ["--kubeconfig", m.kubeconfig, "--context", m.context, ...args],
    { input, timeout: 240000 },
  );
const apply = (m, items) =>
  kube(
    m,
    ["apply", "-f", "-"],
    JSON.stringify({ apiVersion: "v1", kind: "List", items }),
  );
const secret = (namespace, name, data) => ({
  apiVersion: "v1",
  kind: "Secret",
  metadata: { namespace, name },
  type: "Opaque",
  data,
});
if (action === "init") {
  const c = JSON.parse(await readFile(resolve(configFile), "utf8"));
  if (
    !c.context?.startsWith("kind-") ||
    !/^ow-service-m4-[a-z0-9]+$/.test(c.namespace)
  )
    throw Error("Dedicated local kind context/namespace required");
  const current = (await kube(c, ["config", "current-context"])).trim();
  if (current !== c.context) throw Error("Kubecontext mismatch");
  // The connection file must use the gateway DNS address reachable from pods, not localhost.
  const m = await init(state, c.connectionFile, c.image, c.apiPort ?? 58444);
  await writeFile(join(state, "input/probe"), randomBytes(32).toString("hex"), {
    mode: 0o600,
    flag: "wx",
  });
  await writeFile(
    join(state, "input/probe.json"),
    JSON.stringify({
      principal: "readiness",
      role: "observer",
      scope: { kind: "all" },
      expires_at: Math.floor(Date.now() / 1000) + 86400,
    }),
    { mode: 0o600, flag: "wx" },
  );
  const k = {
    ...m,
    kubeconfig: resolve(c.kubeconfig),
    context: c.context,
    namespace: c.namespace,
    imageRef: c.imageRef,
    runnerImage: c.runnerImage,
    release: "ows",
    helm: c.helm ?? "helm",
  };
  if (
    ![k.imageRef, k.runnerImage].every((v) => /@sha256:[a-f0-9]{64}$/.test(v))
  )
    throw Error("Imported image digests required");
  await writeFile(join(state, "kubernetes.json"), JSON.stringify(k, null, 2), {
    mode: 0o600,
  });
} else {
  const m = JSON.parse(await readFile(join(state, "kubernetes.json"), "utf8"));
  if (m.destroyed)
    throw Error(
      "Deployment destroyed; create a new state directory and identity",
    );
  if (m.destroying && action !== "destroy")
    throw Error("Destruction in progress; resume destroy with the same ID");
  if (["install", "fixtures", "database-fixture"].includes(action)) {
    const names = JSON.parse(
      await kube(m, ["get", "namespaces", "-o", "json"]),
    ).items;
    const existing = names.find((n) => n.metadata.name === m.namespace);
    if (
      existing &&
      existing.metadata.labels?.["openworker.io/qualification"] !== m.id
    )
      throw Error("Namespace belongs to another deployment");
    if (!existing)
      await apply(m, [
        {
          apiVersion: "v1",
          kind: "Namespace",
          metadata: {
            name: m.namespace,
            labels: { "openworker.io/qualification": m.id },
          },
        },
      ]);
    const input = join(state, "input");
    const config = JSON.parse(
      await readFile(join(input, "config.json"), "utf8"),
    );
    const read = async (name) =>
      (await readFile(join(input, name))).toString("base64");
    const probe = (await readFile(join(input, "probe"), "utf8")).trim();
    // The readiness key is internal and bounded; supply/rotate its Secret in a long-lived deployment.
    const probeSpec = JSON.parse(
      await readFile(join(input, "probe.json"), "utf8"),
    );
    const fixtureSecrets = [
      secret(m.namespace, "ow-service-database", {
        "database-url": await read("database-url"),
        "pg-ca.crt": await read("pg-ca.crt"),
      }),
      secret(m.namespace, "ow-service-postgres", {
        "tls.crt": await read("pg.crt"),
        "tls.key": await read("pg.key"),
        password: await read("pg-password"),
      }),
      secret(m.namespace, "ow-service-tls", {
        "tls.crt": await read("api.crt"),
        "tls.key": await read("api.key"),
      }),
      secret(m.namespace, "ow-service-openshell", {
        "ca.crt": await read("openshell/ca.crt"),
        "tls.crt": await read("openshell/tls.crt"),
        "tls.key": await read("openshell/tls.key"),
      }),
      secret(m.namespace, "ow-service-bootstrap", {
        "admin-token": await read("admin-token"),
        probe: Buffer.from(probe).toString("base64"),
        "probe.json": Buffer.from(JSON.stringify(probeSpec)).toString("base64"),
      }),
      secret(m.namespace, "ow-service-probe", {
        "curl.conf": Buffer.from(
          `header = "Authorization: Bearer ${probe}"\n`,
        ).toString("base64"),
      }),
    ];
    if (action !== "database-fixture" && config.scripted === false) {
      const provider = config.model_provider;
      if (!["openai", "anthropic"].includes(provider))
        throw Error("Unsupported live model provider");
      fixtureSecrets.push(
        secret(m.namespace, "ow-service-model", {
          [provider]: await read(`model-${provider}`),
        }),
      );
    }
    await apply(
      m,
      action === "database-fixture"
        ? fixtureSecrets.filter(
            (s) => s.metadata.name === "ow-service-postgres",
          )
        : fixtureSecrets,
    );
    await apply(m, [
      {
        apiVersion: "v1",
        kind: "PersistentVolumeClaim",
        metadata: { namespace: m.namespace, name: "postgres-data" },
        spec: {
          accessModes: ["ReadWriteOnce"],
          resources: { requests: { storage: "1Gi" } },
        },
      },
      {
        apiVersion: "v1",
        kind: "Service",
        metadata: { namespace: m.namespace, name: "postgres" },
        spec: { selector: { app: "postgres" }, ports: [{ port: 5432 }] },
      },
      {
        apiVersion: "apps/v1",
        kind: "Deployment",
        metadata: { namespace: m.namespace, name: "postgres" },
        spec: {
          replicas: 1,
          selector: { matchLabels: { app: "postgres" } },
          template: {
            metadata: { labels: { app: "postgres" } },
            spec: {
              automountServiceAccountToken: false,
              securityContext: { fsGroup: 70 },
              containers: [
                {
                  name: "postgres",
                  image:
                    "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
                  args: [
                    "-c",
                    "ssl=on",
                    "-c",
                    "ssl_cert_file=/tls/tls.crt",
                    "-c",
                    "ssl_key_file=/tls/tls.key",
                    "-c",
                    "shared_buffers=16MB",
                  ],
                  env: [
                    { name: "POSTGRES_USER", value: "openworker" },
                    { name: "POSTGRES_DB", value: "openworker" },
                    { name: "POSTGRES_PASSWORD_FILE", value: "/tls/password" },
                  ],
                  volumeMounts: [
                    { name: "tls", mountPath: "/tls", readOnly: true },
                    { name: "data", mountPath: "/var/lib/postgresql/data" },
                  ],
                  readinessProbe: {
                    exec: {
                      command: [
                        "pg_isready",
                        "-U",
                        "openworker",
                        "-d",
                        "openworker",
                      ],
                    },
                    periodSeconds: 2,
                  },
                  resources: {
                    requests: { cpu: "100m", memory: "128Mi" },
                    limits: { memory: "512Mi", cpu: "1" },
                  },
                },
              ],
              volumes: [
                {
                  name: "tls",
                  secret: {
                    secretName: "ow-service-postgres",
                    defaultMode: 0o440,
                  },
                },
                {
                  name: "data",
                  persistentVolumeClaim: { claimName: "postgres-data" },
                },
              ],
            },
          },
        },
      },
    ]);
    await kube(m, [
      "-n",
      m.namespace,
      "rollout",
      "status",
      "deployment/postgres",
      "--timeout=120s",
    ]);
    const values = localValues(config, m);
    await writeFile(
      join(state, "values.json"),
      JSON.stringify(values, null, 2),
      { mode: 0o600 },
    );
    if (action === "install")
      await run(
        m.helm,
        [
          "upgrade",
          "--install",
          m.release,
          resolve("deploy/service/chart"),
          "--kubeconfig",
          m.kubeconfig,
          "--kube-context",
          m.context,
          "--namespace",
          m.namespace,
          "-f",
          join(state, "values.json"),
          "--wait",
          "--wait-for-jobs",
          "--timeout",
          "180s",
        ],
        { inherit: true },
      );
    console.log(
      JSON.stringify({
        namespace: m.namespace,
        url: m.url,
        cliConfig: join(state, "cli.json"),
        portForward: `kubectl --kubeconfig ${m.kubeconfig} --context ${m.context} -n ${m.namespace} port-forward service/ows-api ${m.port}:443 --address 127.0.0.1`,
      }),
    );
  } else if (action === "destroy") {
    if (configFile !== m.id)
      throw Error("destroy requires the exact deployment ID");
    const raw = await kube(m, [
      "get",
      "namespace",
      m.namespace,
      "--ignore-not-found=true",
      "-o",
      "json",
    ]);
    if (raw.trim()) {
      const ns = JSON.parse(raw);
      if (ns.metadata.labels?.["openworker.io/qualification"] !== m.id)
        throw Error("Namespace ownership mismatch");
      if (!m.destroying) {
        const values = JSON.parse(
          await readFile(join(state, "values.json"), "utf8"),
        );
        const schema = values.config.schema;
        if (!/^ow_[a-z0-9_]{1,45}$/.test(schema)) throw Error("Invalid schema");
        const count = () =>
          kube(m, [
            "-n",
            m.namespace,
            "exec",
            "deployment/postgres",
            "--",
            "psql",
            "-U",
            "openworker",
            "-d",
            "openworker",
            "-Atc",
            `SELECT (SELECT count(*) FROM ${schema}.environments WHERE data->>'phase' IS DISTINCT FROM 'deleted')+(SELECT count(*) FROM ${schema}.sessions WHERE NOT COALESCE((data->>'closed')::boolean,FALSE))`,
          ]);
        if ((await count()).trim() !== "0")
          throw Error(
            "Close sessions and delete environments before destroying this namespace",
          );
        const api = JSON.parse(
          await kube(m, [
            "-n",
            m.namespace,
            "get",
            "deployment",
            `${m.release}-api`,
            "-o",
            "json",
          ]),
        );
        await kube(m, [
          "-n",
          m.namespace,
          "scale",
          `deployment/${m.release}-api`,
          "--replicas=0",
        ]);
        await kube(m, [
          "-n",
          m.namespace,
          "wait",
          "--for=delete",
          "pod",
          "-l",
          `app=${m.release}-api`,
          "--timeout=90s",
        ]);
        if ((await count()).trim() !== "0") {
          await kube(m, [
            "-n",
            m.namespace,
            "scale",
            `deployment/${m.release}-api`,
            `--replicas=${api.spec.replicas}`,
          ]);
          throw Error(
            "New work arrived before ingress stopped; API restored, cleanup refused",
          );
        }
        m.destroying = true;
        await writeFile(
          join(state, "kubernetes.json"),
          JSON.stringify(m, null, 2),
          { mode: 0o600 },
        );
      }
      await kube(m, [
        "delete",
        "namespace",
        m.namespace,
        "--wait=true",
        "--timeout=180s",
      ]);
    } else if (!m.destroying)
      throw Error("Namespace missing without a recorded destruction intent");
    m.destroyed = true;
    delete m.destroying;
    await writeFile(
      join(state, "kubernetes.json"),
      JSON.stringify(m, null, 2),
      { mode: 0o600 },
    );
    console.log(
      "Dedicated namespace and PVC removed; OpenShell and other namespaces untouched. Private local files remain for operator disposal.",
    );
  } else if (action === "status")
    console.log(
      await kube(m, ["-n", m.namespace, "get", "pods,deployments,jobs"]),
    );
  else
    throw Error(
      "Unsupported action; cleanup requires explicit sandbox deletion and namespace ownership check",
    );
}
