// Isolated local OTLP/recording qualification. Never deploy this fixture as a
// production stack. No cloud/model credentials, volumes or existing services.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, readFile, writeFile, rm, chmod } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID, createHash } from "node:crypto";
import {
  collectorRecords,
  compareCollector,
  compareLangfuse,
} from "./export-contract.mjs";
const exec = promisify(execFile),
  repo = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const root = resolve(process.argv[2] || ""),
  vendor = process.argv[3] === "--langfuse";
assert(
  process.argv[2] && (!process.argv[3] || vendor),
  "Usage: qualify-exports.mjs NEW_GITIGNORED_ROOT [--langfuse]",
);
await mkdir(root, { mode: 0o700 });
assert.equal(
  (
    await exec("git", ["check-ignore", join(root, "private")], { cwd: repo })
  ).stdout.trim(),
  join(root, "private"),
);
const privateDir = join(root, "private");
await mkdir(privateDir, { mode: 0o700 });
const id = randomUUID().replaceAll("-", "").slice(0, 12),
  label = "openworker.export-qualification",
  net = `ow-export-${id}`,
  owned = [];
const pins = JSON.parse(
  await readFile(new URL("export-qualification-images.json", import.meta.url)),
);
const probe = resolve(
  process.env.OW_EXPORT_PROBE ||
    join(repo, "harness/target/debug/examples/export_probe"),
);
const fixtureSecrets = new Set();
function sanitize(text) {
  for (const secret of fixtureSecrets)
    text = text.replaceAll(secret, "[REDACTED_FIXTURE]");
  return text;
}
const report = {
  version: 1,
  at: new Date().toISOString(),
  passed: false,
  scope: "local Docker and synthetic metadata, no paid model/tool execution",
  images: { collector: pins.collector },
  checks: [],
  cleanup: {},
  vendorEnabled: vendor,
};
const docker = async (...args) =>
  (
    await exec("docker", args, { timeout: 60000, maxBuffer: 2 * 1024 * 1024 })
  ).stdout.trim();
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
// Keep abort/deadline timers alive even if Undici has only unreferenced sockets.
const lifetime = setInterval(() => {}, 1000);
function stage(message) {
  console.log(message);
}
async function waitFor(fn, seconds, label) {
  const until = Date.now() + seconds * 1000;
  let last;
  while (Date.now() < until) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (e) {
      last = e;
    }
    await pause(300);
  }
  throw Error(
    `Timed out: ${label}${last ? " (inspect private fixture logs)" : ""}`,
  );
}
async function jsonFile(file, value) {
  await writeFile(file, JSON.stringify(value, null, 2) + "\n", {
    mode: 0o600,
    flag: "wx",
  });
}
async function envFile(role, values) {
  const file = join(privateDir, role + ".env");
  for (const v of Object.values(values)) assert(!String(v).includes("\n"));
  await writeFile(
    file,
    Object.entries(values)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
    { mode: 0o600, flag: "wx" },
  );
  return file;
}
async function create(role, image, args = [], command = []) {
  assert.match(image, /@sha256:[a-f0-9]{64}$/);
  await docker("image", "inspect", image);
  const name = `ow-export-${role}-${id}`;
  owned.push(name);
  await writeFile(
    join(root, "ownership.json"),
    JSON.stringify({ id, label, network: net, containers: owned }, null, 2),
    { mode: 0o600 },
  );
  await docker(
    "create",
    "--pull=never",
    "--name",
    name,
    "--label",
    `${label}=${id}`,
    "--network",
    net,
    "--network-alias",
    role,
    "--log-opt",
    "max-size=5m",
    "--log-opt",
    "max-file=2",
    ...args,
    image,
    ...command,
  );
  // Docker Desktop's isolated bridge does not publish host ports. Only the
  // two loopback ingress containers also join the default bridge; data services
  // remain internal-only. Collector has file-only export; Langfuse telemetry/AI
  // features are explicitly disabled. No user/cloud/model secrets are supplied.
  if (args.includes("-p")) {
    await docker("network", "connect", "bridge", name);
    report.loopbackIngressBridge = true;
  }
  await docker("start", name);
  return name;
}
async function origin(name, port) {
  const value = JSON.parse(await docker("inspect", name))[0];
  const p = value.NetworkSettings.Ports[port + "/tcp"];
  assert.equal(p.length, 1);
  assert.equal(p[0].HostIp, "127.0.0.1");
  return `http://127.0.0.1:${p[0].HostPort}`;
}
async function runProbe(name, endpoint, authorization = null) {
  const config = join(privateDir, name + ".json");
  await jsonFile(config, {
    endpoint,
    deployment: "m5-qualification",
    signals: authorization ? ["traces"] : ["traces", "metrics", "logs"],
    acknowledgement: authorization ? "langfuse_v4" : "otlp",
    headers: authorization
      ? [
          { name: "Authorization", value_env: "OW_QUAL_EXPORT_AUTH" },
          { name: "x-langfuse-ingestion-version", value: "4" },
        ]
      : [],
    content: { capture: "off" },
  });
  const env = { ...process.env };
  for (const k of Object.keys(env))
    if (
      k.startsWith("AWS_") ||
      /^(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY$/.test(k)
    )
      delete env[k];
  if (authorization) env.OW_QUAL_EXPORT_AUTH = authorization;
  const output = join(root, name);
  const result = await exec(probe, [config, output], {
    env,
    timeout: 60000,
    maxBuffer: 1048576,
  });
  await writeFile(join(root, name + ".log"), result.stdout + result.stderr, {
    mode: 0o600,
  });
  const facts = JSON.parse(await readFile(join(output, "probe.json")));
  assert(
    facts.exporter.every((s) => s.export_failures === 0 && s.dropped === 0),
    "Unexpected exporter loss",
  );
  assert.equal(
    facts.recording.conflicts +
      facts.recording.dropped +
      facts.recording.failed_writes +
      facts.recording.rejected,
    0,
  );
  return {
    recording: JSON.parse(await readFile(join(output, "records.json"))),
    facts,
    summary: JSON.parse(await readFile(join(output, "summary.json"))),
  };
}
async function collector() {
  stage("Starting pinned collector");
  const out = join(root, "collector");
  await mkdir(out, { mode: 0o700 });
  const config = join(privateDir, "collector.yaml");
  await writeFile(
    config,
    `receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 0.0.0.0:4318\nprocessors:\n  memory_limiter:\n    check_interval: 1s\n    limit_mib: 64\n  batch:\n    timeout: 100ms\nexporters:\n  file:\n    path: /out/collector.jsonl\n    format: json\n    flush_interval: 100ms\nservice:\n  telemetry:\n    logs:\n      level: warn\n  pipelines:\n    traces:\n      receivers: [otlp]\n      processors: [memory_limiter, batch]\n      exporters: [file]\n    metrics:\n      receivers: [otlp]\n      processors: [memory_limiter, batch]\n      exporters: [file]\n    logs:\n      receivers: [otlp]\n      processors: [memory_limiter, batch]\n      exporters: [file]\n`,
    { mode: 0o600 },
  );
  const name = await create(
    "collector",
    pins.collector,
    [
      "--memory=128m",
      "--cpus=1",
      "--user",
      `${process.getuid()}:${process.getgid()}`,
      "--read-only",
      "--cap-drop=ALL",
      "--security-opt=no-new-privileges",
      "-p",
      "127.0.0.1::4318",
      "--mount",
      `type=bind,source=${config},target=/config.yaml,readonly`,
      "--mount",
      `type=bind,source=${out},target=/out`,
    ],
    ["--config=/config.yaml"],
  );
  const endpoint = await origin(name, 4318);
  await waitFor(
    async () => {
      const r = await fetch(endpoint + "/v1/traces", {
        signal: AbortSignal.timeout(1000),
      });
      return r.status === 405;
    },
    30,
    "collector receiver",
  );
  stage("Sending recording/OTLP probe to collector");
  const result = await runProbe("collector-probe", endpoint);
  const text = await waitFor(
    async () => {
      try {
        const data = await readFile(join(out, "collector.jsonl"), "utf8");
        const wire = collectorRecords(
          data
            .trim()
            .split("\n")
            .filter(Boolean)
            .map((s) => JSON.parse(s)),
        );
        return wire.spans.length === result.recording.steps.length &&
          wire.logs.length === result.recording.steps.length &&
          wire.metrics.length
          ? data
          : null;
      } catch {
        return null;
      }
    },
    30,
    "collector file flush",
  );
  assert(
    !/PRIVATE|fixture-model|fixture\.read_file|sk-notareal/.test(text),
    "Default-off privacy failure",
  );
  const wire = collectorRecords(
    text
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((s) => JSON.parse(s)),
  );
  const registry = JSON.parse(
    await readFile(join(repo, "harness/telemetry/attributes.json")),
  );
  report.collector = compareCollector(result.recording, wire, registry);
  report.collector.probe = result.facts;
  report.collector.summary = {
    input: result.summary.usage.input,
    output: result.summary.usage.output,
  };
  report.checks.push(
    "official collector protobuf ingestion, all signals, registry and recording reconciliation",
    "default-off content/name exclusion, typed generations, request-only usage and resume links",
  );
  stage("PASS collector reconciliation");
}
async function langfuse() {
  stage("Starting isolated Langfuse dependencies");
  Object.assign(
    report.images,
    Object.fromEntries(Object.entries(pins).filter(([k]) => k !== "collector")),
  );
  const secret = (bytes = 24) => {
    const value = randomBytes(bytes).toString("hex");
    fixtureSecrets.add(value);
    return value;
  };
  const pw = secret(),
    redisPassword = secret(),
    chPassword = secret(),
    s3Password = secret(),
    publicKey = "pk-lf-" + secret(),
    secretKey = "sk-lf-" + secret();
  const pg = await create("postgres", pins.postgres, [
    "--memory=384m",
    "--cpus=1",
    "--env-file",
    await envFile("postgres", {
      POSTGRES_PASSWORD: pw,
      POSTGRES_USER: "fixture",
      POSTGRES_DB: "langfuse",
    }),
  ]);
  await waitFor(
    async () => {
      await docker("exec", pg, "pg_isready", "-U", "fixture", "-d", "langfuse");
      return true;
    },
    30,
    "postgres readiness",
  );
  const redisConfig = join(privateDir, "redis.conf");
  await writeFile(
    redisConfig,
    `bind 0.0.0.0\nprotected-mode yes\nrequirepass ${redisPassword}\nsave ""\nappendonly no\n`,
    { mode: 0o600 },
  );
  await create(
    "redis",
    pins.redis,
    [
      "--memory=128m",
      "--cpus=1",
      "--user",
      `${process.getuid()}:${process.getgid()}`,
      "--entrypoint",
      "redis-server",
      "--mount",
      `type=bind,source=${redisConfig},target=/config/redis.conf,readonly`,
    ],
    ["/config/redis.conf"],
  );
  const chConfig = join(privateDir, "clickhouse.xml");
  await writeFile(
    chConfig,
    "<clickhouse><max_server_memory_usage>1000000000</max_server_memory_usage><max_thread_pool_size>128</max_thread_pool_size><background_schedule_pool_size>16</background_schedule_pool_size><background_buffer_flush_schedule_pool_size>4</background_buffer_flush_schedule_pool_size><background_pool_size>16</background_pool_size><background_move_pool_size>4</background_move_pool_size><background_fetches_pool_size>4</background_fetches_pool_size><background_common_pool_size>4</background_common_pool_size><background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size><background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size></clickhouse>",
    { mode: 0o644 },
  );
  await chmod(chConfig, 0o644);
  const ch = await create("clickhouse", pins.clickhouse, [
    "--memory=1536m",
    "--cpus=2",
    "--user",
    "101:101",
    "--env-file",
    await envFile("clickhouse", {
      CLICKHOUSE_DB: "default",
      CLICKHOUSE_USER: "fixture",
      CLICKHOUSE_PASSWORD: chPassword,
    }),
    "--mount",
    `type=bind,source=${chConfig},target=/etc/clickhouse-server/config.d/fixture.xml,readonly`,
  ]);
  await waitFor(
    async () => {
      await docker(
        "exec",
        ch,
        "/bin/sh",
        "-c",
        'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "SELECT 1"',
      );
      return true;
    },
    45,
    "clickhouse readiness",
  );
  const minio = await create(
    "minio",
    pins.minio,
    [
      "--memory=256m",
      "--cpus=1",
      "--env-file",
      await envFile("minio", {
        MINIO_ROOT_USER: "fixture",
        MINIO_ROOT_PASSWORD: s3Password,
      }),
    ],
    ["server", "/data"],
  );
  const mc = await create(
    "mc",
    pins.mc,
    [
      "--memory=128m",
      "--entrypoint",
      "/bin/sh",
      "--env-file",
      await envFile("mc", {
        MINIO_ROOT_USER: "fixture",
        MINIO_ROOT_PASSWORD: s3Password,
      }),
    ],
    [
      "-c",
      'until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do sleep 1; done; mc mb --ignore-existing local/langfuse',
    ],
  );
  await docker("wait", mc);
  assert.equal(
    JSON.parse(await docker("inspect", mc))[0].State.ExitCode,
    0,
    "minio bucket initialization",
  );
  const common = {
    HOSTNAME: "0.0.0.0",
    DATABASE_URL: `postgresql://fixture:${pw}@postgres:5432/langfuse`,
    DIRECT_URL: `postgresql://fixture:${pw}@postgres:5432/langfuse`,
    SALT: secret(),
    ENCRYPTION_KEY: secret(32),
    NEXTAUTH_SECRET: secret(),
    NEXTAUTH_URL: "http://localhost:3000",
    TELEMETRY_ENABLED: "false",
    LANGFUSE_IN_APP_AGENT_ENABLED: "false",
    LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES: "false",
    CLICKHOUSE_MIGRATION_URL: "clickhouse://clickhouse:9000",
    CLICKHOUSE_URL: "http://clickhouse:8123",
    CLICKHOUSE_USER: "fixture",
    CLICKHOUSE_PASSWORD: chPassword,
    CLICKHOUSE_CLUSTER_ENABLED: "false",
    REDIS_HOST: "redis",
    REDIS_PORT: "6379",
    REDIS_AUTH: redisPassword,
    LANGFUSE_S3_EVENT_UPLOAD_BUCKET: "langfuse",
    LANGFUSE_S3_EVENT_UPLOAD_REGION: "us-east-1",
    LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: "fixture",
    LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: s3Password,
    LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: "http://minio:9000",
    LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true",
    LANGFUSE_S3_EVENT_UPLOAD_PREFIX: "events/",
    LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: "langfuse",
    LANGFUSE_S3_MEDIA_UPLOAD_REGION: "us-east-1",
    LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: "fixture",
    LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: s3Password,
    LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: "http://minio:9000",
    LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true",
    LANGFUSE_S3_BATCH_EXPORT_ENABLED: "false",
    LANGFUSE_INGESTION_QUEUE_DELAY_MS: "0",
    LANGFUSE_INGESTION_CLICKHOUSE_WRITE_INTERVAL_MS: "1000",
    NODE_OPTIONS: "--max-old-space-size=768",
  };
  const web = await create("langfuse", pins.langfuse, [
    "--memory=1280m",
    "--cpus=2",
    "-p",
    "127.0.0.1::3000",
    "--env-file",
    await envFile("langfuse", {
      ...common,
      LANGFUSE_INIT_ORG_ID: "qualification-org",
      LANGFUSE_INIT_ORG_NAME: "Qualification",
      LANGFUSE_INIT_PROJECT_ID: "qualification-project",
      LANGFUSE_INIT_PROJECT_NAME: "Fixture",
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: publicKey,
      LANGFUSE_INIT_PROJECT_SECRET_KEY: secretKey,
      LANGFUSE_INIT_USER_EMAIL: "fixture@example.invalid",
      LANGFUSE_INIT_USER_NAME: "Fixture",
      LANGFUSE_INIT_USER_PASSWORD: secret(),
    }),
  ]);
  const worker = await create("worker", pins.worker, [
    "--memory=1280m",
    "--cpus=2",
    "--env-file",
    await envFile("worker", common),
  ]);
  const endpoint = await origin(web, 3000),
    authorization =
      "Basic " + Buffer.from(publicKey + ":" + secretKey).toString("base64");
  fixtureSecrets.add(authorization);
  fixtureSecrets.add(authorization.slice(6));
  stage("Waiting for Langfuse migrations and project initialization");
  await waitFor(
    async () => {
      const r = await fetch(endpoint + "/api/public/projects", {
        headers: { Authorization: authorization },
        signal: AbortSignal.timeout(3000),
      });
      return r.ok;
    },
    240,
    "Langfuse project readiness",
  );
  const ack = await fetch(endpoint + "/api/public/otel/v1/traces", {
    method: "POST",
    headers: {
      Authorization: authorization,
      "content-type": "application/x-protobuf",
      accept: "application/x-protobuf",
      "x-langfuse-ingestion-version": "4",
    },
    body: Buffer.alloc(0),
    signal: AbortSignal.timeout(5000),
  });
  const ackBody = Buffer.from(await ack.arrayBuffer());
  report.langfusePreflight = {
    status: ack.status,
    contentType: ack.headers.get("content-type"),
    bytes: ackBody.length,
  };
  try {
    report.langfusePreflight.jsonKeys = Object.keys(
      JSON.parse(ackBody.toString()),
    );
  } catch {}
  stage(
    "Langfuse acknowledgement: " + JSON.stringify(report.langfusePreflight),
  );
  stage("Exporting once to actual Langfuse OTLP ingestion");
  const result = await runProbe(
    "langfuse-probe",
    endpoint + "/api/public/otel",
    authorization,
  );
  const trace = result.recording.steps[0].step.trace_id;
  const from = new Date(
      Math.min(...result.recording.steps.map((r) => r.step.started_ms)) - 1000,
    ).toISOString(),
    to = new Date(
      Math.max(...result.recording.steps.map((r) => r.step.ended_ms)) + 1000,
    ).toISOString();
  const path =
    "/api/public/v2/observations?" +
    new URLSearchParams({
      traceId: trace,
      limit: "100",
      fields: "core,basic,usage,io,model,metadata",
      fromStartTime: from,
      toStartTime: to,
    });
  const observations = await waitFor(
    async () => {
      const r = await fetch(endpoint + path, {
        headers: { Authorization: authorization },
        signal: AbortSignal.timeout(5000),
      });
      const value = await r.json();
      if (r.ok)
        await jsonFileOnce(join(privateDir, "last-observations.json"), value);
      return r.ok && value.data?.length === result.recording.steps.length
        ? value.data
        : null;
    },
    90,
    "Langfuse observation processing",
  );
  assert(
    !JSON.stringify(observations).includes("PRIVATE"),
    "Langfuse default-off privacy failure",
  );
  await jsonFile(join(root, "langfuse-observations.json"), observations);
  report.langfuse = compareLangfuse(observations, result.recording);
  report.langfuse.probe = result.facts;
  report.checks.push(
    "actual Langfuse v4 OTLP auth/ingestion and v2 observation reads",
    "vendor generation classification, pseudonymous session grouping, exclusive cache buckets and no content",
  );
  stage("PASS actual Langfuse reconciliation");
}
async function jsonFileOnce(file, value) {
  await writeFile(file, JSON.stringify(value), { mode: 0o600 });
}
try {
  report.probeSha256 = createHash("sha256")
    .update(await readFile(probe))
    .digest("hex");
  await jsonFile(join(root, "ownership.json"), { id, label, network: net });
  await docker(
    "network",
    "create",
    "--internal",
    "--label",
    `${label}=${id}`,
    net,
  );
  report.internalNetwork = JSON.parse(
    await docker("network", "inspect", net),
  )[0].Internal;
  assert(report.internalNetwork);
  await collector();
  if (vendor) await langfuse();
  report.passed = true;
} catch (e) {
  report.error = e.message;
  throw e;
} finally {
  clearInterval(lifetime);
  for (const name of owned.reverse()) {
    let info;
    try {
      info = JSON.parse(await docker("inspect", name))[0];
    } catch {
      continue;
    }
    assert.equal(
      info.Config.Labels[label],
      id,
      "Refusing unowned container cleanup",
    );
    try {
      const logs = sanitize(
        await exec("docker", ["logs", name], {
          timeout: 10000,
          maxBuffer: 2 * 1024 * 1024,
        }).then((r) => r.stdout + r.stderr),
      );
      assert([...fixtureSecrets].every((secret) => !logs.includes(secret)));
      await writeFile(join(root, name + ".log"), logs, { mode: 0o600 });
    } catch {}
    await docker("rm", "-fv", info.Id);
  }
  try {
    const info = JSON.parse(await docker("network", "inspect", net))[0];
    assert.equal(info.Labels[label], id);
    await docker("network", "rm", net);
    report.cleanup.networkRemoved = true;
  } catch (e) {
    if (report.passed) throw e;
  }
  await rm(privateDir, { recursive: true, force: true });
  report.cleanup.generatedSecretsRemoved = true;
  report.cleanup.ownedContainersRemoved = true;
  report.cleanup.unrelatedResourcesModified = false;
  await jsonFile(join(root, "qualification.json"), report);
  console.log(
    JSON.stringify({
      passed: report.passed,
      checks: report.checks.length,
      vendorEnabled: vendor,
      cleanup: report.cleanup,
    }),
  );
}
