// Local kind qualification only. No cloud access or default kubecontext mutation.
import { spawn } from "node:child_process";
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID, createHash } from "node:crypto";
export const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export const root = join(repo, ".ow-helm");
export const namespace = "ow-helm-eval",
  shellNamespace = "ow-helm-shell";
export async function run(program, args, { input, timeout = 240000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(program, args, { stdio: ["pipe", "pipe", "pipe"] });
    let out = "",
      err = "";
    child.stdout.on("data", (b) => (out += b));
    child.stderr.on("data", (b) => {
      if (err.length < 4096) err += b;
    });
    child.on("error", () => reject(Error(program + " could not start")));
    child.stdin.on("error", () => {});
    child.stdin.end(input);
    const timer = setTimeout(() => child.kill("SIGTERM"), timeout);
    child.on("close", (code) => {
      clearTimeout(timer);
      code === 0
        ? resolve(out)
        : reject(
            Error(
              program +
                " failed; inspect the scoped pod/job (output withheld to protect secrets)",
            ),
          );
    });
  });
}
export async function settings() {
  const m = JSON.parse(await readFile(join(root, "local.json"), "utf8"));
  if (!m.context.startsWith("kind-") || !m.kubeconfig || !m.helm || !m.image)
    throw Error("Explicit kind context, kubeconfig, Helm and image required");
  const current = (
    await run("kubectl", [
      "--kubeconfig",
      m.kubeconfig,
      "config",
      "current-context",
    ])
  ).trim();
  if (current !== m.context) throw Error("Refusing unexpected kubecontext");
  return m;
}
export const kubectl = (m, args, input) =>
  run(
    "kubectl",
    ["--kubeconfig", m.kubeconfig, "--context", m.context, ...args],
    { input },
  );
export const apply = (m, items) =>
  kubectl(
    m,
    ["apply", "-f", "-"],
    JSON.stringify({ apiVersion: "v1", kind: "List", items }),
  );
export async function ownNamespace(m, name) {
  const all = JSON.parse(
    await kubectl(m, ["get", "namespaces", "-o", "json"]),
  ).items;
  const existing = all.find((n) => n.metadata.name === name);
  if (
    existing &&
    existing.metadata.annotations?.["openworker.dev/deployment"] !== m.id
  )
    throw Error(
      "Refusing namespace not owned by this local deployment: " + name,
    );
  await apply(m, [
    {
      apiVersion: "v1",
      kind: "Namespace",
      metadata: {
        name,
        labels: { "openworker.dev/fixture": "helm-local" },
        annotations: { "openworker.dev/deployment": m.id },
      },
    },
  ]);
}
export async function openshell(m) {
  await ownNamespace(m, shellNamespace);
  const directory = join(root, "charts"),
    archive = join(directory, "helm-chart-0.1.3.tgz");
  await mkdir(directory, { recursive: true });
  try {
    await access(archive);
  } catch {
    await run(m.helm, [
      "pull",
      "oci://ghcr.io/nvidia/openshell/helm-chart",
      "--version",
      "0.1.3",
      "--destination",
      directory,
    ]);
  }
  if (
    createHash("sha256")
      .update(await readFile(archive))
      .digest("hex") !==
    "5a82e27d6cf4089fa16d96c87d9fc0fe62f65fa05341465bc749ba5ab91abb33"
  )
    throw Error("OpenShell archive differs from the qualified pin");
  await run(m.helm, [
    "upgrade",
    "--install",
    "ow-shell",
    archive,
    "--kubeconfig",
    m.kubeconfig,
    "--kube-context",
    m.context,
    "--namespace",
    shellNamespace,
    "-f",
    join(repo, "deploy/helm/openshell-local.yaml"),
    "--wait",
    "--timeout",
    "180s",
  ]);
}
const meta = (name) => ({
  name,
  namespace,
  labels: { "openworker.dev/fixture": "helm-local" },
});
const secret = (name, data) => ({
  apiVersion: "v1",
  kind: "Secret",
  metadata: meta(name),
  type: "Opaque",
  stringData: data,
});
const configmap = (name, data) => ({
  apiVersion: "v1",
  kind: "ConfigMap",
  metadata: meta(name),
  data,
});
const svc = (name, port, targetPort = port) => ({
  apiVersion: "v1",
  kind: "Service",
  metadata: meta(name),
  spec: { selector: { app: name }, ports: [{ port, targetPort }] },
});
const pvc = (name) => ({
  apiVersion: "v1",
  kind: "PersistentVolumeClaim",
  metadata: meta(name),
  spec: {
    accessModes: ["ReadWriteOnce"],
    resources: { requests: { storage: "1Gi" } },
  },
});
export async function certificate(name, dns) {
  const folder = join(root, "private"),
    key = join(folder, name + ".key"),
    crt = join(folder, name + ".crt");
  await run("openssl", [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "30",
    "-keyout",
    join(folder, name + ".ca.key"),
    "-out",
    join(folder, name + ".ca.crt"),
    "-subj",
    "/CN=" + name + "-local-CA",
    "-addext",
    "basicConstraints=critical,CA:TRUE",
  ]);
  const csr = join(folder, name + ".csr"),
    ext = join(folder, name + ".ext");
  await run("openssl", [
    "req",
    "-new",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    key,
    "-out",
    csr,
    "-subj",
    "/CN=" + dns[0],
  ]);
  await writeFile(
    ext,
    "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=" +
      dns.map((d) => "DNS:" + d).join(",") +
      "\n",
    { mode: 0o600 },
  );
  await run("openssl", [
    "x509",
    "-req",
    "-in",
    csr,
    "-CA",
    join(folder, name + ".ca.crt"),
    "-CAkey",
    join(folder, name + ".ca.key"),
    "-CAcreateserial",
    "-days",
    "30",
    "-out",
    crt,
    "-extfile",
    ext,
  ]);
  return { key: await readFile(key, "utf8"), crt: await readFile(crt, "utf8") };
}
export async function init(m) {
  await mkdir(root, { recursive: true, mode: 0o700 });
  await mkdir(join(root, "private"), { mode: 0o700 }); // Refuse regeneration.
  for (const field of ["kubeconfig", "helm"]) m[field] = resolve(m[field]);
  m.id = randomUUID();
  await writeFile(join(root, "local.json"), JSON.stringify(m, null, 2), {
    mode: 0o600,
    flag: "wx",
  });
  await settings();
  for (const [name, value] of Object.entries({
    admin: randomBytes(32).toString("hex"),
    probe: randomBytes(32).toString("hex"),
    pg: randomBytes(32).toString("hex"),
    s3: randomBytes(32).toString("hex"),
  })) {
    await writeFile(join(root, "private", name), value, {
      mode: 0o600,
      flag: "wx",
    });
  }
  for (const [name, dns] of [
    ["pg", ["postgres", `postgres.${namespace}.svc.cluster.local`]],
    ["objects", ["objects", `objects.${namespace}.svc.cluster.local`]],
    ["api", ["localhost", `ow-openworker.${namespace}.svc.cluster.local`]],
  ])
    await certificate(name, dns);
}
export async function fixtures(m) {
  const read = (name) => readFile(join(root, "private", name), "utf8");
  const pg = await read("pg"),
    admin = await read("admin"),
    probe = await read("probe"),
    s3 = await read("s3");
  await ownNamespace(m, namespace);
  const shell = JSON.parse(
    await kubectl(m, [
      "-n",
      shellNamespace,
      "get",
      "secret",
      "openshell-client-tls",
      "-o",
      "json",
    ]),
  );
  const shellData = Object.fromEntries(
    Object.entries(shell.data).map(([k, v]) => [
      k,
      Buffer.from(v, "base64").toString(),
    ]),
  );
  const objectsConf = `pid /tmp/nginx.pid; error_log /dev/stderr warn; events {} http { access_log off; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy; server { listen 8443 ssl; ssl_certificate /cert/tls.crt; ssl_certificate_key /cert/tls.key; ssl_protocols TLSv1.2 TLSv1.3; client_max_body_size 65m; location / { proxy_pass http://127.0.0.1:8333; proxy_set_header Host $http_host; proxy_request_buffering off; proxy_buffering off; } } }`;
  await apply(m, [
    secret("database", {
      url: `postgresql://openworker:${pg}@postgres.${namespace}.svc.cluster.local:5432/openworker`,
    }),
    secret("postgres-login", {
      POSTGRES_PASSWORD: pg,
      POSTGRES_USER: "openworker",
      POSTGRES_DB: "openworker",
    }),
    secret("database-ca", { "ca.crt": await read("pg.ca.crt") }),
    secret("postgres-tls", {
      "tls.crt": await read("pg.crt"),
      "tls.key": await read("pg.key"),
    }),
    secret("objects-tls", {
      "tls.crt": await read("objects.crt"),
      "tls.key": await read("objects.key"),
    }),
    secret("object-ca", { "ca.crt": await read("objects.ca.crt") }),
    secret("object-login", {
      AWS_ACCESS_KEY_ID: "local-harness",
      AWS_SECRET_ACCESS_KEY: s3,
    }),
    secret("object-config", {
      "s3.json": JSON.stringify({
        identities: [
          {
            name: "harness",
            credentials: [{ accessKey: "local-harness", secretKey: s3 }],
            actions: [
              "Read:openworker-artifacts",
              "Write:openworker-artifacts",
              "List:openworker-artifacts",
            ],
          },
        ],
      }),
    }),
    secret("api-tls", {
      "tls.crt": await read("api.crt"),
      "tls.key": await read("api.key"),
    }),
    secret("openshell-client", shellData),
    secret("probe", {
      "curl.conf": `header = "Authorization: Bearer ${probe}"\n`,
    }),
    secret("bootstrap", {
      admin: admin,
      probe: probe,
      "probe.json": JSON.stringify({
        principal: "health-probe",
        role: "observer",
        scope: { kind: "owned" },
      }),
    }),
    configmap("objects-proxy", { "nginx.conf": objectsConf }),
    configmap("postgres-config", {
      "pg_hba.conf":
        "local all all trust\nhostssl all all all scram-sha-256\nhostnossl all all all reject\n",
    }),
    pvc("postgres-data"),
    pvc("objects-data"),
    svc("postgres", 5432),
    svc("objects", 8443),
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: meta("postgres"),
      spec: {
        replicas: 1,
        strategy: { type: "Recreate" },
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
                  "cp /cert/* /tmp/; chown postgres:postgres /tmp/tls.*; chmod 600 /tmp/tls.key; exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/tls.crt -c ssl_key_file=/tmp/tls.key -c hba_file=/pg-config/pg_hba.conf",
                ],
                envFrom: [{ secretRef: { name: "postgres-login" } }],
                env: [
                  { name: "PGDATA", value: "/var/lib/postgresql/data/pgdata" },
                ],
                readinessProbe: {
                  exec: { command: ["pg_isready", "-U", "openworker"] },
                  periodSeconds: 3,
                },
                resources: {
                  requests: { cpu: "50m", memory: "64Mi" },
                  limits: { cpu: "500m", memory: "256Mi" },
                },
                volumeMounts: [
                  { name: "data", mountPath: "/var/lib/postgresql/data" },
                  { name: "cert", mountPath: "/cert", readOnly: true },
                  { name: "config", mountPath: "/pg-config", readOnly: true },
                ],
              },
            ],
            volumes: [
              {
                name: "data",
                persistentVolumeClaim: { claimName: "postgres-data" },
              },
              { name: "cert", secret: { secretName: "postgres-tls" } },
              { name: "config", configMap: { name: "postgres-config" } },
            ],
          },
        },
      },
    },
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: meta("objects"),
      spec: {
        replicas: 1,
        strategy: { type: "Recreate" },
        selector: { matchLabels: { app: "objects" } },
        template: {
          metadata: { labels: { app: "objects" } },
          spec: {
            containers: [
              {
                name: "objects",
                image:
                  "chrislusf/seaweedfs@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d",
                args: [
                  "mini",
                  "-dir=/data",
                  "-s3.config=/auth/s3.json",
                  "-bucket=openworker-artifacts",
                  "-admin.ui=false",
                  "-webdav=false",
                  "-volume.max=4",
                ],
                resources: {
                  requests: { cpu: "50m", memory: "128Mi" },
                  limits: { cpu: "500m", memory: "512Mi" },
                },
                volumeMounts: [
                  { name: "data", mountPath: "/data" },
                  { name: "auth", mountPath: "/auth", readOnly: true },
                ],
              },
              {
                name: "tls",
                image:
                  "nginx@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236",
                command: [
                  "nginx",
                  "-c",
                  "/config/nginx.conf",
                  "-g",
                  "daemon off;",
                ],
                readinessProbe: { tcpSocket: { port: 8443 } },
                volumeMounts: [
                  { name: "cert", mountPath: "/cert", readOnly: true },
                  { name: "config", mountPath: "/config", readOnly: true },
                ],
              },
            ],
            volumes: [
              {
                name: "data",
                persistentVolumeClaim: { claimName: "objects-data" },
              },
              { name: "auth", secret: { secretName: "object-config" } },
              { name: "cert", secret: { secretName: "objects-tls" } },
              { name: "config", configMap: { name: "objects-proxy" } },
            ],
          },
        },
      },
    },
    {
      apiVersion: "networking.k8s.io/v1",
      kind: "NetworkPolicy",
      metadata: meta("backing-services"),
      spec: {
        podSelector: {
          matchExpressions: [
            { key: "app", operator: "In", values: ["postgres", "objects"] },
          ],
        },
        policyTypes: ["Ingress"],
        ingress: [
          {
            from: [{ podSelector: {} }],
            ports: [
              { protocol: "TCP", port: 5432 },
              { protocol: "TCP", port: 8443 },
            ],
          },
        ],
      },
    },
  ]);
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "status",
    "deployment/postgres",
    "--timeout=180s",
  ]);
  await kubectl(m, [
    "-n",
    namespace,
    "rollout",
    "status",
    "deployment/objects",
    "--timeout=180s",
  ]);
  // Separate local fixtures, not bundled production dependencies.
  const config = {
    database_env: "OW_SERVER_DATABASE_URL",
    schema: "ow_helm",
    database_ca: "/database-ca/ca.crt",
    state_root: "/state",
    bundle: "/opt/openworker/bundle",
    runner: "/opt/openworker/openworker",
    model: "fixture",
    scripted: true,
    api_token_file: "/bootstrap/admin",
    actor: "local-admin",
    openshell: {
      identity: "helm-local",
      endpoint: `https://ow-helm-gateway.${shellNamespace}.svc.cluster.local:8080`,
      certificates: "/openshell",
      workspace: "default",
      image: m.runnerImage,
    },
    artifacts: {
      kind: "s3",
      deployment: m.id,
      bucket: "openworker-artifacts",
      region: "us-east-1",
      endpoint: `https://objects.${namespace}.svc.cluster.local:8443`,
      ca_file: "/object-ca/ca.crt",
    },
  };
  const values = {
    image: m.image,
    config,
    secrets: {
      database: "database",
      databaseCA: "database-ca",
      openshell: "openshell-client",
      apiTLS: "api-tls",
      probe: "probe",
      objectStore: "object-login",
      objectStoreCA: "object-ca",
    },
    network: { serviceNamespaces: [shellNamespace] },
  };
  await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
    mode: 0o600,
  });
}
export async function helm(m, extra = []) {
  return run(m.helm, [
    "upgrade",
    "--install",
    "ow",
    join(repo, "deploy/helm/openworker"),
    "--kubeconfig",
    m.kubeconfig,
    "--kube-context",
    m.context,
    "--namespace",
    namespace,
    "-f",
    join(root, "values.json"),
    ...extra,
  ]);
}
export async function job(m, name, args, bootstrap = false) {
  const dep = JSON.parse(
    await kubectl(m, [
      "-n",
      namespace,
      "get",
      "deployment",
      "ow-openworker-worker",
      "-o",
      "json",
    ]),
  );
  const spec = dep.spec.template.spec;
  const c = spec.containers[0];
  c.name = "admin";
  c.args = args;
  delete c.livenessProbe;
  delete c.readinessProbe;
  delete c.envFrom;
  if (!args.includes("environment-create")) {
    c.volumeMounts = c.volumeMounts.filter((v) => v.name !== "openshell");
    spec.volumes = spec.volumes.filter((v) => v.name !== "openshell");
  }
  // Controller-local state is ephemeral; DB, artifacts and sandbox PVC are durable.
  if (bootstrap) {
    spec.volumes.push({
      name: "bootstrap",
      secret: { secretName: "bootstrap", defaultMode: 288 },
    });
    for (const key of ["admin", "probe", "probe.json"])
      c.volumeMounts.push({
        name: "bootstrap",
        mountPath: "/bootstrap/" + key,
        subPath: key,
        readOnly: true,
      });
  }
  c.env = c.env.filter((e) => e.name !== "POD_NAME");
  spec.restartPolicy = "Never";
  await apply(m, [
    {
      apiVersion: "batch/v1",
      kind: "Job",
      metadata: meta(name),
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
    namespace,
    "wait",
    "--for=condition=complete",
    "job/" + name,
    "--timeout=150s",
  ]);
  return kubectl(m, ["-n", namespace, "logs", "job/" + name, "-c", "admin"]);
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const action = process.argv[2];
  if (action === "init") {
    await init(JSON.parse(await readFile(resolve(process.argv[3]), "utf8")));
    console.log(
      "Private local state created. Existing credentials will never be regenerated.",
    );
  } else {
    const m = await settings();
    if (action === "openshell") {
      await openshell(m);
      console.log("Pinned authenticated OpenShell release ready.");
    } else if (action === "upgrade") {
      await helm(m, ["--wait", "--timeout", "180s"]);
      console.log("OpenWorker upgraded; bootstrap was not rerun.");
    } else if (action === "fixtures") {
      await fixtures(m);
      console.log("Local backing fixtures ready.");
    } else if (action === "install") {
      const deployments = JSON.parse(
        await kubectl(m, [
          "-n",
          namespace,
          "get",
          "deployments",
          "-l",
          "app.kubernetes.io/instance=ow",
          "-o",
          "json",
        ]),
      ).items;
      if (deployments.some((d) => (d.spec.replicas || 0) > 0))
        throw Error(
          "Use upgrade for a running installation; install will not scale it down",
        );
      await helm(m, ["--set", "api.replicas=0", "--set", "worker.replicas=0"]);
      await job(m, "ow-migrate", [
        "--config",
        "/config/reference.json",
        "migrate",
      ]);
      await job(
        m,
        "ow-bootstrap",
        ["--config", "/config/reference.json", "auth-bootstrap"],
        true,
      );
      await job(
        m,
        "ow-probe-key",
        [
          "--config",
          "/config/reference.json",
          "auth-issue",
          "--spec",
          "/bootstrap/probe.json",
          "--token-file",
          "/bootstrap/probe",
          "--request-id",
          "health-probe-001",
        ],
        true,
      );
      await helm(m, ["--wait", "--timeout", "180s"]);
      console.log("OpenWorker installed; APIs/workers run as Kubernetes pods.");
    } else if (action === "environment") {
      const result = await job(m, "ow-environment", [
        "--config",
        "/config/reference.json",
        "environment-create",
        "--request-id",
        "helm-environment-001",
        "--shared",
      ]);
      await writeFile(join(root, "environment.json"), result, { mode: 0o600 });
      console.log("Environment receipt saved privately.");
    } else
      throw Error(
        "Use init CONFIG.json | openshell | fixtures | install | upgrade | environment",
      );
  }
}
