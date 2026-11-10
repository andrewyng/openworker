// Persistent, local-only Langfuse demo. No cloud calls, model keys, or default
// kubecontext changes. Retains PVCs and credentials across repeated `up` calls.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { run } from "../service/stack.mjs";
const [action, directory, configFile] = process.argv.slice(2),
  root = resolve(directory || ".");
assert(
  ["init", "up", "status"].includes(action) && directory,
  "Usage: langfuse-kubernetes-local.mjs init STATE CONFIG.json | up STATE | status STATE",
);
const pins = JSON.parse(
  await readFile(new URL("export-qualification-images.json", import.meta.url)),
);
const label = "openworker.io/local-langfuse";
if (action === "init") {
  const c = JSON.parse(await readFile(resolve(configFile), "utf8"));
  assert(
    c.context?.startsWith("kind-") &&
      /^ow-langfuse-[a-z0-9]+$/.test(c.namespace),
    "Dedicated local kind namespace required",
  );
  const kubeconfig = resolve(c.kubeconfig);
  assert.equal(
    (
      await run("kubectl", [
        "--kubeconfig",
        kubeconfig,
        "config",
        "current-context",
      ])
    ).trim(),
    c.context,
  );
  await mkdir(root, { mode: 0o700 });
  assert.equal(
    (await run("git", ["check-ignore", join(root, "private")])).trim(),
    join(root, "private"),
    "State must be gitignored",
  );
  await mkdir(join(root, "private"), { mode: 0o700 });
  const secret = (n = 24) => randomBytes(n).toString("hex");
  const credentials = {
    postgres: secret(),
    redis: secret(),
    clickhouse: secret(),
    s3: secret(),
    salt: secret(),
    encryption: secret(32),
    nextauth: secret(),
    publicKey: "pk-lf-" + secret(),
    secretKey: "sk-lf-" + secret(),
    email: "local-demo@example.invalid",
    password: secret(),
  };
  const m = {
    version: 1,
    id: randomUUID(),
    kubeconfig,
    context: c.context,
    namespace: c.namespace,
    projectId: "openworker-local",
    orgId: "openworker-local",
    port: c.port ?? 59600,
    images: pins,
  };
  await writeFile(
    join(root, "private/credentials.json"),
    JSON.stringify(credentials),
    { mode: 0o600, flag: "wx" },
  );
  await writeFile(join(root, "installation.json"), JSON.stringify(m, null, 2), {
    mode: 0o600,
    flag: "wx",
  });
  console.log(
    JSON.stringify({
      initialized: true,
      namespace: m.namespace,
      credentialsFile: join(root, "private/credentials.json"),
    }),
  );
} else {
  const m = JSON.parse(await readFile(join(root, "installation.json"), "utf8"));
  assert(
    m.context.startsWith("kind-") &&
      /^ow-langfuse-[a-z0-9]+$/.test(m.namespace),
  );
  const kube = (args, input) =>
    run(
      "kubectl",
      ["--kubeconfig", m.kubeconfig, "--context", m.context, ...args],
      { input, timeout: 300000 },
    );
  const current = await kube([
    "get",
    "namespace",
    m.namespace,
    "--ignore-not-found",
    "-o",
    "json",
  ]);
  if (current.trim())
    assert.equal(
      JSON.parse(current).metadata.labels[label],
      m.id,
      "Namespace ownership mismatch",
    );
  if (action === "status") {
    console.log(await kube(["-n", m.namespace, "get", "pods,pvc,services"]));
    process.exit(0);
  }
  const s = JSON.parse(
    await readFile(join(root, "private/credentials.json"), "utf8"),
  );
  const ns = m.namespace,
    metadata = (name) => ({ name, namespace: ns, labels: { [label]: m.id } });
  const items = [
    {
      apiVersion: "v1",
      kind: "Namespace",
      metadata: { name: ns, labels: { [label]: m.id } },
    },
  ];
  const add = (kind, name, spec) =>
    items.push({
      apiVersion: kind === "Deployment" ? "apps/v1" : "v1",
      kind,
      metadata: metadata(name),
      ...spec,
    });
  function pvc(name, size) {
    add("PersistentVolumeClaim", name, {
      spec: {
        accessModes: ["ReadWriteOnce"],
        resources: { requests: { storage: size } },
      },
    });
  }
  function secret(name, data) {
    add("Secret", name, { type: "Opaque", stringData: data });
  }
  function config(name, data) {
    add("ConfigMap", name, { data });
  }
  function service(name, port, targetPort = port, app = name) {
    add("Service", name, {
      spec: { selector: { app }, ports: [{ port, targetPort }] },
    });
  }
  function deployment(
    name,
    image,
    container,
    volumes = [],
    { memory = "256Mi", cpu = "1", uid, gid } = {},
  ) {
    add("Deployment", name, {
      spec: {
        replicas: 1,
        strategy: { type: "Recreate" },
        selector: { matchLabels: { app: name } },
        template: {
          metadata: { labels: { app: name, [label]: m.id } },
          spec: {
            automountServiceAccountToken: false,
            terminationGracePeriodSeconds: 40,
            ...(gid ? { securityContext: { fsGroup: gid } } : {}),
            containers: [
              {
                name,
                image,
                imagePullPolicy: "IfNotPresent",
                securityContext: {
                  allowPrivilegeEscalation: false,
                  capabilities: { drop: ["ALL"] },
                  ...(uid
                    ? { runAsUser: uid, runAsGroup: gid, runAsNonRoot: true }
                    : {}),
                },
                resources: {
                  requests: { cpu: "50m", memory: "64Mi" },
                  limits: { cpu, memory },
                },
                ...container,
              },
            ],
            volumes,
          },
        },
      },
    });
  }
  pvc("postgres-data", "1Gi");
  pvc("redis-data", "512Mi");
  pvc("clickhouse-data", "4Gi");
  pvc("minio-data", "2Gi");
  secret("postgres", {
    POSTGRES_USER: "langfuse",
    POSTGRES_DB: "langfuse",
    POSTGRES_PASSWORD: s.postgres,
    PGDATA: "/data/pgdata",
  });
  deployment(
    "lf-postgres",
    m.images.postgres,
    {
      envFrom: [{ secretRef: { name: "postgres" } }],
      volumeMounts: [{ name: "data", mountPath: "/data" }],
      readinessProbe: {
        exec: { command: ["pg_isready", "-U", "langfuse", "-d", "langfuse"] },
        periodSeconds: 3,
      },
    },
    [{ name: "data", persistentVolumeClaim: { claimName: "postgres-data" } }],
    { uid: 70, gid: 70, memory: "384Mi" },
  );
  service("lf-postgres", 5432);
  secret("redis", {
    "redis.conf": `bind 0.0.0.0\nprotected-mode yes\nrequirepass ${s.redis}\nappendonly yes\ndir /data\n`,
  });
  deployment(
    "lf-redis",
    m.images.redis,
    {
      command: ["redis-server", "/config/redis.conf"],
      volumeMounts: [
        { name: "config", mountPath: "/config", readOnly: true },
        { name: "data", mountPath: "/data" },
      ],
      readinessProbe: { tcpSocket: { port: 6379 }, periodSeconds: 3 },
    },
    [
      { name: "config", secret: { secretName: "redis", defaultMode: 0o440 } },
      { name: "data", persistentVolumeClaim: { claimName: "redis-data" } },
    ],
    { uid: 999, gid: 999, memory: "128Mi" },
  );
  service("lf-redis", 6379);
  secret("clickhouse", {
    CLICKHOUSE_USER: "fixture",
    CLICKHOUSE_PASSWORD: s.clickhouse,
    CLICKHOUSE_DB: "default",
  });
  config("clickhouse", {
    // The global pool also serves foreground/async-insert work. A 128-thread
    // fixture cap starves the real UI after migrations/background tasks start;
    // per-query parallelism and memory remain independently bounded below.
    "users.xml":
      "<clickhouse><profiles><default><max_threads>2</max_threads><max_insert_threads>1</max_insert_threads><max_memory_usage>300000000</max_memory_usage></default></profiles></clickhouse>",
    "fixture.xml":
      "<clickhouse><max_server_memory_usage>1800000000</max_server_memory_usage><mark_cache_size>67108864</mark_cache_size><uncompressed_cache_size>33554432</uncompressed_cache_size><max_thread_pool_size>1024</max_thread_pool_size><background_schedule_pool_size>16</background_schedule_pool_size><background_buffer_flush_schedule_pool_size>4</background_buffer_flush_schedule_pool_size><background_pool_size>16</background_pool_size><background_move_pool_size>4</background_move_pool_size><background_fetches_pool_size>4</background_fetches_pool_size><background_common_pool_size>4</background_common_pool_size><background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size><background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size></clickhouse>",
  });
  deployment(
    "lf-clickhouse",
    m.images.clickhouse,
    {
      envFrom: [{ secretRef: { name: "clickhouse" } }],
      volumeMounts: [
        {
          name: "config",
          mountPath: "/etc/clickhouse-server/users.d/demo.xml",
          subPath: "users.xml",
          readOnly: true,
        },
        { name: "data", mountPath: "/var/lib/clickhouse" },
        { name: "logs", mountPath: "/var/log/clickhouse-server" },
        {
          name: "config",
          mountPath: "/etc/clickhouse-server/config.d/fixture.xml",
          subPath: "fixture.xml",
          readOnly: true,
        },
      ],
      readinessProbe: {
        httpGet: { path: "/ping", port: 8123 },
        periodSeconds: 5,
        timeoutSeconds: 5,
      },
    },
    [
      { name: "data", persistentVolumeClaim: { claimName: "clickhouse-data" } },
      { name: "logs", emptyDir: {} },
      { name: "config", configMap: { name: "clickhouse" } },
    ],
    { uid: 101, gid: 101, memory: "2560Mi", cpu: "2" },
  );
  service("lf-clickhouse", 8123);
  service("lf-clickhouse-native", 9000, 9000, "lf-clickhouse");
  secret("minio", {
    MINIO_ROOT_USER: "fixture",
    MINIO_ROOT_PASSWORD: s.s3,
    GOMAXPROCS: "2",
  });
  deployment(
    "lf-minio",
    m.images.minio,
    {
      args: ["server", "/data"],
      envFrom: [{ secretRef: { name: "minio" } }],
      volumeMounts: [{ name: "data", mountPath: "/data" }],
      readinessProbe: {
        httpGet: { path: "/minio/health/live", port: 9000 },
        periodSeconds: 5,
        timeoutSeconds: 5,
        failureThreshold: 5,
      },
    },
    [{ name: "data", persistentVolumeClaim: { claimName: "minio-data" } }],
    { uid: 1000, gid: 1000, memory: "512Mi", cpu: "2" },
  );
  service("lf-minio", 9000);
  secret("langfuse", {
    HOSTNAME: "0.0.0.0",
    DATABASE_URL: `postgresql://langfuse:${s.postgres}@lf-postgres:5432/langfuse`,
    DIRECT_URL: `postgresql://langfuse:${s.postgres}@lf-postgres:5432/langfuse`,
    SALT: s.salt,
    ENCRYPTION_KEY: s.encryption,
    NEXTAUTH_SECRET: s.nextauth,
    NEXTAUTH_URL: `http://127.0.0.1:${m.port}`,
    TELEMETRY_ENABLED: "false",
    LANGFUSE_IN_APP_AGENT_ENABLED: "false",
    LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES: "false",
    CLICKHOUSE_MIGRATION_URL: "clickhouse://lf-clickhouse-native:9000",
    CLICKHOUSE_URL: "http://lf-clickhouse:8123",
    CLICKHOUSE_USER: "fixture",
    CLICKHOUSE_PASSWORD: s.clickhouse,
    CLICKHOUSE_CLUSTER_ENABLED: "false",
    REDIS_HOST: "lf-redis",
    REDIS_PORT: "6379",
    REDIS_AUTH: s.redis,
    LANGFUSE_S3_EVENT_UPLOAD_BUCKET: "langfuse",
    LANGFUSE_S3_EVENT_UPLOAD_REGION: "us-east-1",
    LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: "fixture",
    LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: s.s3,
    LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: "http://lf-minio:9000",
    LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true",
    LANGFUSE_S3_EVENT_UPLOAD_PREFIX: "events/",
    LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: "langfuse",
    LANGFUSE_S3_MEDIA_UPLOAD_REGION: "us-east-1",
    LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: "fixture",
    LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: s.s3,
    LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: "http://lf-minio:9000",
    LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true",
    LANGFUSE_S3_BATCH_EXPORT_ENABLED: "false",
    LANGFUSE_INGESTION_QUEUE_DELAY_MS: "0",
    LANGFUSE_INGESTION_CLICKHOUSE_WRITE_INTERVAL_MS: "1000",
    NODE_OPTIONS: "--max-old-space-size=768",
    LANGFUSE_INIT_ORG_ID: m.orgId,
    LANGFUSE_INIT_ORG_NAME: "OpenWorker Local",
    LANGFUSE_INIT_PROJECT_ID: m.projectId,
    LANGFUSE_INIT_PROJECT_NAME: "OpenWorker Harness",
    LANGFUSE_INIT_PROJECT_PUBLIC_KEY: s.publicKey,
    LANGFUSE_INIT_PROJECT_SECRET_KEY: s.secretKey,
    LANGFUSE_INIT_USER_EMAIL: s.email,
    LANGFUSE_INIT_USER_NAME: "Local demo",
    LANGFUSE_INIT_USER_PASSWORD: s.password,
  });
  // Idempotent local bucket creation. Existing data is never removed by `up`.
  const job = {
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: metadata("lf-bucket"),
    spec: {
      backoffLimit: 4,
      template: {
        spec: {
          restartPolicy: "OnFailure",
          automountServiceAccountToken: false,
          containers: [
            {
              name: "mc",
              image: m.images.mc,
              command: [
                "/bin/sh",
                "-ec",
                'until mc alias set local http://lf-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do sleep 1; done; mc mb --ignore-existing local/langfuse',
              ],
              envFrom: [{ secretRef: { name: "minio" } }],
              resources: {
                requests: { cpu: "20m", memory: "32Mi" },
                limits: { cpu: "1", memory: "128Mi" },
              },
            },
          ],
        },
      },
    },
  };
  items.push(job);
  for (const [name, image] of [
    ["lf-web", m.images.langfuse],
    ["lf-worker", m.images.worker],
  ])
    deployment(
      name,
      image,
      {
        envFrom: [{ secretRef: { name: "langfuse" } }],
        ...(name === "lf-web"
          ? {
              readinessProbe: {
                tcpSocket: { port: 3000 },
                periodSeconds: 3,
                failureThreshold: 120,
              },
            }
          : {}),
      },
      [],
      { memory: "1280Mi", cpu: "2" },
    );
  service("lf-web", 3000);
  await kube(
    ["apply", "-f", "-"],
    JSON.stringify({ apiVersion: "v1", kind: "List", items }),
  );
  for (const name of [
    "lf-postgres",
    "lf-redis",
    "lf-clickhouse",
    "lf-minio",
    "lf-web",
    "lf-worker",
  ])
    await kube([
      "-n",
      ns,
      "rollout",
      "status",
      "deployment/" + name,
      "--timeout=240s",
    ]);
  console.log(
    JSON.stringify({
      ready: true,
      namespace: ns,
      projectId: m.projectId,
      portForward: [
        "kubectl",
        "--kubeconfig",
        m.kubeconfig,
        "--context",
        m.context,
        "-n",
        ns,
        "port-forward",
        "service/lf-web",
        `${m.port}:3000`,
        "--address",
        "127.0.0.1",
      ],
    }),
  );
}
