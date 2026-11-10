import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, randomBytes } from "node:crypto";
import { connect, account, region } from "./client.mjs";
import { name } from "./templates.mjs";
import { run, repo, kubectl, apply } from "../helm/local.mjs";
const root = join(repo, ".ow-aws"),
  ns = "ow-app",
  shell = "ow-shell";
const { aws } = await connect();
const plan = JSON.parse(await readFile(join(root, "plan.json"), "utf8"));
if (Date.now() >= Date.parse(plan.expiry) || plan.account !== account)
  throw Error("Expired or wrong experiment");
const cluster = (await aws(["eks", "describe-cluster", "--name", name]))
  .cluster;
if (
  cluster.status !== "ACTIVE" ||
  cluster.arn !== `arn:aws:eks:${region}:${account}:cluster/${name}`
)
  throw Error("Expected active experiment cluster");
const m = {
  kubeconfig: join(root, "kubeconfig"),
  context: name,
  helm: process.env.OW_HELM || "helm",
};
await writeFile(
  m.kubeconfig,
  JSON.stringify({
    apiVersion: "v1",
    kind: "Config",
    clusters: [
      {
        name,
        cluster: {
          server: cluster.endpoint,
          "certificate-authority-data": cluster.certificateAuthority.data,
        },
      },
    ],
    contexts: [{ name, context: { cluster: name, user: name } }],
    "current-context": name,
    users: [
      {
        name,
        user: {
          exec: {
            apiVersion: "client.authentication.k8s.io/v1beta1",
            command: "aws",
            interactiveMode: "Never",
            args: [
              "eks",
              "get-token",
              "--cluster-name",
              name,
              "--region",
              region,
              "--role-arn",
              `arn:aws:iam::${account}:role/OrganizationAccountAccessRole`,
            ],
          },
        },
      },
    ],
  }),
  { mode: 0o600 },
);
await writeFile(join(root, "kubernetes.json"), JSON.stringify(m, null, 2), {
  mode: 0o600,
});
const meta = (name, namespace = ns) => ({
  name,
  namespace,
  labels: { "openworker.dev/experiment": plan.name },
});
const secret = (name, stringData) => ({
  apiVersion: "v1",
  kind: "Secret",
  metadata: meta(name),
  type: "Opaque",
  stringData,
});
async function own(namespace) {
  const result = JSON.parse(
    await kubectl(m, ["get", "namespaces", "-o", "json"]),
  );
  const n = result.items.find((n) => n.metadata.name === namespace);
  if (n && n.metadata.labels?.["openworker.dev/experiment"] !== name)
    throw Error("Unowned namespace");
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
}
const helm = (extra) =>
  run(m.helm, [
    "upgrade",
    "--install",
    "ow",
    join(repo, "deploy/helm/openworker"),
    "--kubeconfig",
    m.kubeconfig,
    "--kube-context",
    m.context,
    "--namespace",
    ns,
    "-f",
    join(root, "values.json"),
    ...extra,
  ]);
async function key(filename) {
  const path = join(root, filename);
  try {
    return (await readFile(path, "utf8")).trim();
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
    const k = randomBytes(32).toString("hex");
    await writeFile(path, k, { mode: 0o600, flag: "wx" });
    return k;
  }
}
async function job(jobname, args, bootstrap = false) {
  const old = JSON.parse(
    await kubectl(m, ["-n", ns, "get", "jobs", "-o", "json"]),
  ).items.find((j) => j.metadata.name === jobname);
  if (old?.status.succeeded)
    return kubectl(m, ["-n", ns, "logs", "job/" + jobname, "-c", "admin"]);
  if (old)
    throw Error("Existing incomplete job requires inspection: " + jobname);
  const dep = JSON.parse(
    await kubectl(m, [
      "-n",
      ns,
      "get",
      "deployment",
      "ow-openworker-worker",
      "-o",
      "json",
    ]),
  );
  const spec = dep.spec.template.spec,
    c = spec.containers[0];
  const db = (
    await aws([
      "rds",
      "describe-db-instances",
      "--db-instance-identifier",
      name,
    ])
  ).DBInstances[0];
  spec.initContainers = [
    {
      name: "database-ready",
      image:
        "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
      command: [
        "sh",
        "-c",
        'i=0; until pg_isready -q -t 3; do i=$((i+1)); [ "$i" -lt 20 ] || exit 1; sleep 2; done',
      ],
      env: [
        { name: "PGHOST", value: db.Endpoint.Address },
        { name: "PGPORT", value: "5432" },
        { name: "PGUSER", value: "openworker" },
        { name: "PGDATABASE", value: "openworker" },
        { name: "PGSSLMODE", value: "verify-full" },
        { name: "PGSSLROOTCERT", value: "/database-ca/ca.crt" },
      ],
      securityContext: c.securityContext,
      volumeMounts: [
        { name: "database-ca", mountPath: "/database-ca", readOnly: true },
      ],
      resources: {
        requests: { cpu: "20m", memory: "32Mi" },
        limits: { cpu: "100m", memory: "64Mi" },
      },
    },
  ];
  c.name = "admin";
  c.args = args;
  delete c.envFrom;
  delete c.livenessProbe;
  delete c.readinessProbe;
  c.env = c.env.filter((e) => e.name !== "POD_NAME");
  if (
    !args.includes("environment-create") &&
    !args.includes("environment-reconcile")
  ) {
    spec.volumes = spec.volumes.filter((v) => v.name !== "openshell");
    c.volumeMounts = c.volumeMounts.filter((v) => v.name !== "openshell");
  }
  if (bootstrap) {
    spec.volumes.push({
      name: "bootstrap",
      secret: { secretName: "bootstrap", defaultMode: 288 },
    });
    for (const k of [
      "admin",
      "probe",
      "probe.json",
      "operator",
      "operator.json",
    ])
      c.volumeMounts.push({
        name: "bootstrap",
        mountPath: "/bootstrap/" + k,
        subPath: k,
        readOnly: true,
      });
  }
  spec.restartPolicy = "Never";
  await apply(m, [
    {
      apiVersion: "batch/v1",
      kind: "Job",
      metadata: meta(jobname),
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
    ns,
    "wait",
    "--for=condition=complete",
    "job/" + jobname,
    "--timeout=150s",
  ]);
  return kubectl(m, ["-n", ns, "logs", "job/" + jobname, "-c", "admin"]);
}
const action = process.argv[2];
if (action === "prerequisites") {
  const stack = (
    await aws(["cloudformation", "describe-stacks", "--stack-name", name])
  ).Stacks[0];
  if (
    stack.StackStatus !== "CREATE_COMPLETE" ||
    !stack.Tags?.some((t) => t.Key === "Project" && t.Value === name)
  )
    throw Error(
      "Wait for the owned infrastructure stack, including healthy initial add-ons, before applying strict CNI prerequisites",
    );
  await own(ns);
  await own(shell);
  // Trusted cluster controllers require access to Kubernetes/AWS. Don't apply
  // namespace-wide allow policies to OpenShell or application/sandbox namespaces.
  await apply(m, [
    {
      apiVersion: "networking.k8s.io/v1",
      kind: "NetworkPolicy",
      metadata: meta("cluster-controllers", "kube-system"),
      spec: {
        podSelector: {},
        policyTypes: ["Ingress", "Egress"],
        ingress: [{}],
        egress: [{}],
      },
    },
  ]);
  const update = await aws([
    "eks",
    "update-addon",
    "--cluster-name",
    name,
    "--addon-name",
    "vpc-cni",
    "--resolve-conflicts",
    "PRESERVE",
    "--configuration-values",
    JSON.stringify({
      enableNetworkPolicy: "true",
      env: { NETWORK_POLICY_ENFORCING_MODE: "strict" },
    }),
  ]);
  console.log("Strict CNI update: " + update.update.id);
  await apply(m, [
    {
      apiVersion: "storage.k8s.io/v1",
      kind: "StorageClass",
      metadata: {
        name: "ow-gp3",
        annotations: { "storageclass.kubernetes.io/is-default-class": "true" },
      },
      provisioner: "ebs.csi.aws.com",
      parameters: {
        type: "gp3",
        encrypted: "true",
        tagSpecification_1: `Project=${name}`,
      },
      reclaimPolicy: "Delete",
      volumeBindingMode: "WaitForFirstConsumer",
      allowVolumeExpansion: true,
    },
  ]);
  await kubectl(m, [
    "apply",
    "--server-side",
    "-f",
    "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.6/sandbox.yaml",
  ]);
  // Controller's namespace is created by the upstream manifest.
  const deployments = JSON.parse(
    await kubectl(m, ["get", "deployments", "-A", "-o", "json"]),
  );
  const controller = deployments.items.find((d) =>
    d.metadata.name.includes("agent-sandbox"),
  );
  if (controller)
    await apply(m, [
      {
        apiVersion: "networking.k8s.io/v1",
        kind: "NetworkPolicy",
        metadata: meta("controller-access", controller.metadata.namespace),
        spec: {
          podSelector: { matchLabels: controller.spec.selector.matchLabels },
          policyTypes: ["Ingress", "Egress"],
          ingress: [],
          egress: [{}],
        },
      },
    ]);
  console.log(
    "Cluster prerequisites applied. Verify CNI rollout and real deny/allow before tools.",
  );
} else if (action === "openshell" || action === "gateway-policy") {
  const archive = join(repo, ".ow-helm/charts/helm-chart-0.1.3.tgz");
  if (
    createHash("sha256")
      .update(await readFile(archive))
      .digest("hex") !==
    "5a82e27d6cf4089fa16d96c87d9fc0fe62f65fa05341465bc749ba5ab91abb33"
  )
    throw Error("OpenShell archive pin mismatch");
  // Gateway/controller require API and storage access; sandbox-specific policies
  // remain independent. Select only the gateway's chart label, not all pods.
  await apply(m, [
    {
      apiVersion: "networking.k8s.io/v1",
      kind: "NetworkPolicy",
      metadata: meta("gateway-access", shell),
      spec: {
        podSelector: {
          matchLabels: { "app.kubernetes.io/instance": "ow-shell" },
        },
        policyTypes: ["Ingress", "Egress"],
        ingress: [
          {
            from: [
              {
                namespaceSelector: {
                  matchLabels: { "kubernetes.io/metadata.name": ns },
                },
              },
              {
                podSelector: {
                  matchLabels: { "openshell.ai/boundary-role": "supervisor" },
                },
              },
            ],
            ports: [{ protocol: "TCP", port: 8080 }],
          },
        ],
        egress: [{}],
      },
    },
  ]);
  if (action === "gateway-policy") {
    console.log(
      "Gateway permits authenticated harness and supervisor control traffic on 8080.",
    );
    process.exit(0);
  }
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
    shell,
    "-f",
    join(repo, "deploy/helm/openshell-local.yaml"),
    "--wait",
    "--timeout",
    "180s",
  ]);
  console.log("Pinned OpenShell release installed.");
} else if (action === "configure") {
  const existing = JSON.parse(
    await kubectl(m, ["-n", ns, "get", "deployments", "-o", "json"]),
  ).items;
  if (
    existing.some(
      (d) => d.metadata.name.startsWith("ow-openworker") && d.spec.replicas > 0,
    )
  )
    throw Error(
      "Configure is first-install only; refuse to stop a running application",
    );
  const images = JSON.parse(await readFile(join(root, "images.json"), "utf8"));
  const db = (
    await aws([
      "rds",
      "describe-db-instances",
      "--db-instance-identifier",
      name,
    ])
  ).DBInstances[0];
  if (db.DBInstanceStatus !== "available" || db.PubliclyAccessible)
    throw Error("Private database not ready");
  const login = JSON.parse(
    (
      await aws([
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        db.MasterUserSecret.SecretArn,
      ])
    ).SecretString,
  );
  const ca = await (
    await fetch(
      `https://truststore.pki.rds.amazonaws.com/${region}/${region}-bundle.pem`,
    )
  ).text();
  if (!ca.includes("BEGIN CERTIFICATE")) throw Error("No RDS CA");
  const crt = join(root, "api.crt"),
    priv = join(root, "api.key");
  try {
    await readFile(crt);
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
    await run("openssl", [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-days",
      "2",
      "-keyout",
      join(root, "ca.key"),
      "-out",
      join(root, "ca.crt"),
      "-subj",
      "/CN=Task-Desk-Experiment-CA",
    ]);
    await run("openssl", [
      "req",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-keyout",
      priv,
      "-out",
      join(root, "api.csr"),
      "-subj",
      "/CN=ow-openworker.ow-app.svc.cluster.local",
    ]);
    await writeFile(
      join(root, "api.ext"),
      "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,DNS:ow-openworker.ow-app.svc.cluster.local\n",
      { mode: 0o600 },
    );
    await run("openssl", [
      "x509",
      "-req",
      "-in",
      join(root, "api.csr"),
      "-CA",
      join(root, "ca.crt"),
      "-CAkey",
      join(root, "ca.key"),
      "-CAcreateserial",
      "-days",
      "2",
      "-out",
      crt,
      "-extfile",
      join(root, "api.ext"),
    ]);
  }
  const gateway = JSON.parse(
    await kubectl(m, [
      "-n",
      shell,
      "get",
      "secret",
      "openshell-client-tls",
      "-o",
      "json",
    ]),
  );
  const admin = await key("admin"),
    probe = await key("probe"),
    operator = await key("operator"),
    access = await key("access");
  await apply(m, [
    secret("database", {
      url: `postgresql://${login.username}:${encodeURIComponent(login.password)}@${db.Endpoint.Address}:5432/openworker`,
    }),
    secret("database-ca", { "ca.crt": ca }),
    secret("api-tls", {
      "tls.crt": await readFile(crt, "utf8"),
      "tls.key": await readFile(priv, "utf8"),
    }),
    secret("api-ca", {
      "ca.crt": await readFile(join(root, "ca.crt"), "utf8"),
    }),
    secret("probe", {
      "curl.conf": `header = "Authorization: Bearer ${probe}"\n`,
    }),
    secret(
      "openshell-client",
      Object.fromEntries(
        Object.entries(gateway.data).map(([k, v]) => [
          k,
          Buffer.from(v, "base64").toString(),
        ]),
      ),
    ),
    secret("bootstrap", {
      admin,
      probe,
      operator,
      "probe.json": JSON.stringify({
        principal: "health-probe",
        role: "observer",
        scope: { kind: "owned" },
      }),
      "operator.json": "{}",
    }),
    secret("task-desk-access", { operator, access }),
    {
      apiVersion: "v1",
      kind: "ServiceAccount",
      metadata: meta("ow-api"),
      automountServiceAccountToken: false,
    },
  ]);
  const config = {
    database_env: "OW_SERVER_DATABASE_URL",
    schema: "ow_taskdesk",
    database_ca: "/database-ca/ca.crt",
    state_root: "/state",
    bundle: "/opt/openworker/bundle",
    runner: "/opt/openworker/openworker",
    model: "fixture",
    scripted: true,
    api_token_file: "/bootstrap/admin",
    actor: "task-desk-admin",
    openshell: {
      identity: name,
      endpoint: `https://ow-helm-gateway.${shell}.svc.cluster.local:8080`,
      certificates: "/openshell",
      workspace: "default",
      image: images.runner,
    },
    artifacts: {
      kind: "s3",
      deployment: name,
      bucket: name + "-" + account,
      region,
    },
  };
  const values = {
    image: images.harness,
    databaseReadiness: { enabled: true, host: db.Endpoint.Address },
    api: { replicas: 0, serviceAccountName: "ow-api" },
    worker: { replicas: 0, slots: 1 },
    config,
    secrets: {
      database: "database",
      databaseCA: "database-ca",
      openshell: "openshell-client",
      apiTLS: "api-tls",
      probe: "probe",
    },
    network: {
      enabled: true,
      clientNamespaces: [],
      serviceNamespaces: [shell],
      extraEgress: [
        {
          to: [{ ipBlock: { cidr: "10.94.10.0/23" } }],
          ports: [{ protocol: "TCP", port: 5432 }],
        },
        {
          to: [
            {
              ipBlock: {
                cidr: "0.0.0.0/0",
                except: [
                  "10.0.0.0/8",
                  "172.16.0.0/12",
                  "192.168.0.0/16",
                  "169.254.0.0/16",
                ],
              },
            },
          ],
          ports: [{ protocol: "TCP", port: 443 }],
        },
      ],
    },
  };
  await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
    mode: 0o600,
  });
  await apply(m, [
    {
      apiVersion: "networking.k8s.io/v1",
      kind: "NetworkPolicy",
      metadata: meta("api-pod-identity"),
      spec: {
        podSelector: {
          matchLabels: {
            "app.kubernetes.io/component": "api",
            "app.kubernetes.io/instance": "ow",
          },
        },
        policyTypes: ["Egress"],
        egress: [
          {
            to: [{ ipBlock: { cidr: "169.254.170.23/32" } }],
            ports: [{ protocol: "TCP", port: 80 }],
          },
        ],
      },
    },
  ]);
  await helm([]);
  console.log("Private configuration installed with zero API/worker replicas.");
} else if (action === "bootstrap") {
  await job("ow-migrate", ["--config", "/config/reference.json", "migrate"]);
  await job(
    "ow-bootstrap",
    ["--config", "/config/reference.json", "auth-bootstrap"],
    true,
  );
  await job(
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
      "probe-v1",
    ],
    true,
  );
  let saved;
  try {
    saved = JSON.parse(await readFile(join(root, "environment.json"), "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const receipt =
    saved?.phase === "ready"
      ? await job("ow-environment-observed", [
          "--config",
          "/config/reference.json",
          "environment-inspect",
          "--environment-id",
          saved.id,
        ])
      : await job("ow-environment", [
          "--config",
          "/config/reference.json",
          "environment-create",
          "--request-id",
          "taskdesk-environment-v1",
          "--shared",
        ]);
  const environment = JSON.parse(receipt);
  if (environment.phase !== "ready")
    throw Error("Reconcile the original environment before starting agents");
  await writeFile(join(root, "environment.json"), receipt, { mode: 0o600 });
  const bootstrap = JSON.parse(
    await kubectl(m, ["-n", ns, "get", "secret", "bootstrap", "-o", "json"]),
  );
  bootstrap.data["operator.json"] = Buffer.from(
    JSON.stringify({
      principal: "task-desk",
      role: "operator",
      scope: { kind: "owned" },
      environments: [environment.id],
    }),
  ).toString("base64");
  delete bootstrap.metadata.managedFields;
  await apply(m, [bootstrap]);
  await job(
    "ow-app-key",
    [
      "--config",
      "/config/reference.json",
      "auth-issue",
      "--spec",
      "/bootstrap/operator.json",
      "--token-file",
      "/bootstrap/operator",
      "--request-id",
      "taskdesk-app-v1",
    ],
    true,
  );
  const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
  values.api.replicas = 2;
  values.worker.replicas = 2;
  await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
    mode: 0o600,
  });
  await helm(["--wait", "--timeout", "180s"]);
  console.log(
    "Harness bootstrapped with environment and scoped application operator.",
  );
} else if (action === "new-fixture-environment") {
  const proof = JSON.parse(
    await readFile(join(root, "absent-original.json"), "utf8"),
  );
  if (
    !proof.providerNotFound ||
    !proof.kubernetesObjectsAbsent ||
    Date.now() - Date.parse(proof.observedAt) > 15 * 60000
  )
    throw Error("Recent authoritative absence inspection required");
  const receipt = await job("ow-environment-v2", [
    "--config",
    "/config/reference.json",
    "environment-create",
    "--request-id",
    "taskdesk-environment-v2",
    "--shared",
  ]);
  const record = JSON.parse(receipt);
  if (record.id === proof.environment_id || record.phase !== "ready")
    throw Error("New test environment not ready");
  await writeFile(join(root, "environment.json"), receipt, { mode: 0o600 });
  console.log(
    "New explicit test environment ready; original unknown receipt retained: " +
      record.id,
  );
} else if (action === "reconcile-environment") {
  const attempt = process.argv[3] || "v1";
  if (!["v1", "v2"].includes(attempt))
    throw Error("Explicit known experiment attempt required");
  const original = JSON.parse(
    await job("ow-environment-" + attempt + "-receipt", [
      "--config",
      "/config/reference.json",
      "environment-create",
      "--request-id",
      "taskdesk-environment-" + attempt,
      "--shared",
    ]),
  );
  if (!/^env_[a-f0-9]{32}$/.test(original.id))
    throw Error("Unexpected original identity");
  const remote = "owr-" + original.id.slice(4, 19);
  const receipt = await job("ow-environment-" + attempt + "-reconciled", [
    "--config",
    "/config/reference.json",
    "environment-reconcile",
    "--environment-id",
    original.id,
    "--remote-name",
    remote,
  ]);
  const observed = JSON.parse(receipt);
  if (observed.id !== original.id || observed.phase !== "ready")
    throw Error("Original environment is not ready");
  await writeFile(join(root, "environment.json"), receipt, { mode: 0o600 });
  console.log(
    "Original environment reconciled without creating a replacement: " +
      observed.id,
  );
} else if (action === "app") {
  const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
  const database = (
    await aws([
      "rds",
      "describe-db-instances",
      "--db-instance-identifier",
      name,
    ])
  ).DBInstances[0];
  values.databaseReadiness = { enabled: true, host: database.Endpoint.Address };
  const images = JSON.parse(await readFile(join(root, "images.json"), "utf8"));
  const environment = JSON.parse(
    await readFile(join(root, "environment.json"), "utf8"),
  );
  values.taskDesk = {
    enabled: true,
    image: images.taskdesk,
    accessSecret: "task-desk-access",
    apiCASecret: "api-ca",
    config: {
      apis: [`https://ow-openworker.${ns}.svc.cluster.local`],
      api_token_file: "/access/operator",
      access_key_file: "/access/access",
      environment_id: environment.id,
      coworker: "task-desk",
      workspace: "/sandbox",
      port: 54350,
      origin: "http://127.0.0.1:54351",
      mode: values.config.scripted ? "scripted-test" : "live",
    },
  };
  await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
    mode: 0o600,
  });
  await helm(["--wait", "--timeout", "180s"]);
  console.log("Task Desk ready for an authenticated Kubernetes port-forward.");
  await mkdir(join(root, "taskdesk-test"), { recursive: true, mode: 0o700 });
  await writeFile(
    join(root, "taskdesk-test/config.json"),
    JSON.stringify(
      { port: 54351, access_key_file: join(root, "access") },
      null,
      2,
    ),
    { mode: 0o600 },
  );
} else
  throw Error(
    "Use prerequisites | openshell | gateway-policy | configure | bootstrap | reconcile-environment v1|v2 | new-fixture-environment | app",
  );
