// Repeatable local qualification. No cloud provisioning or model keys.
import assert from "node:assert/strict";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, writeFile, readFile, unlink } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
const exec = promisify(execFile),
  repo = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const [level, output, ...extra] = process.argv.slice(2);
assert(
  ["fast", "postgres", "cluster"].includes(level) && output,
  "Usage: node deploy/resilience/run.mjs fast|postgres|cluster NEW_IGNORED_OUTPUT [SERVICE_STATE EXACT_SERVICE_ID]",
);
const root = resolve(output);
assert.equal(
  (
    await exec("git", ["check-ignore", join(root, "private")], { cwd: repo })
  ).stdout.trim(),
  join(root, "private"),
  "Output must be gitignored",
);
await mkdir(root, { mode: 0o700 });
const report = {
  version: 1,
  level,
  started: new Date().toISOString(),
  source: (
    await exec("git", ["rev-parse", "HEAD"], { cwd: repo })
  ).stdout.trim(),
  sourceIsDirty: Boolean(
    (await exec("git", ["status", "--porcelain"], { cwd: repo })).stdout.trim(),
  ),
  passed: false,
  checks: [],
  cleanup: {},
};
const env = { ...process.env, CARGO_BUILD_JOBS: "1" };
for (const k of Object.keys(env))
  if (/^(AWS_|GOOGLE_|AZURE_|OPENAI_|ANTHROPIC_|OPENROUTER_)/.test(k))
    delete env[k];
async function check(name, bin, args, variables = {}, timeout = 600000) {
  const child = spawn(bin, args, {
    cwd: repo,
    env: { ...env, ...variables },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  for (const stream of [child.stdout, child.stderr])
    stream.on("data", (b) => {
      log += b;
      if (log.length > 8 * 1024 * 1024) child.kill("SIGTERM");
    });
  const timer = setTimeout(() => child.kill("SIGTERM"), timeout);
  let code;
  try {
    code = await new Promise((ok, no) => {
      child.once("error", no);
      child.once("close", ok);
    });
  } finally {
    clearTimeout(timer);
  }
  await writeFile(join(root, name + ".log"), log, { mode: 0o600, flag: "wx" });
  const totals = [...log.matchAll(/test result: ok\. (\d+) passed/g)].reduce(
    (n, m) => n + Number(m[1]),
    0,
  );
  report.checks.push({ name, passed: code === 0, rustTestsPassed: totals });
  assert.equal(code, 0, `${name} failed; inspect private log`);
  if (bin === "cargo" || name === "postgres-linux-worker-faults")
    assert(totals > 0, `${name} ran no Rust tests`);
  if (name === "postgres-linux-worker-faults")
    assert(
      log.includes(
        "resilience_streaming_worker_death_preserves_budget_and_cursor_without_tool_replay ... ok",
      ),
      "New streaming crash case must actually run",
    );
  console.log("PASS " + name);
}
const cargo = (name, args, variables) =>
  check(
    name,
    "cargo",
    [
      "test",
      "--offline",
      "--locked",
      "--manifest-path",
      "harness/Cargo.toml",
      ...args,
    ],
    variables,
  );
const docker = async (...args) =>
  (
    await exec("docker", args, { timeout: 120000, maxBuffer: 4194304 })
  ).stdout.trim();
async function inspectOptional(name) {
  try {
    return JSON.parse(await docker("inspect", name))[0];
  } catch (e) {
    if (/No such (object|container):/.test(e.stderr ?? "")) return null;
    throw e; // Daemon failure is not proof of resource deletion.
  }
}
try {
  if (level === "fast") {
    await cargo("runtime-unit", ["-p", "openworker-runtime", "--lib"]);
    await cargo("runtime-contracts", [
      "-p",
      "openworker-runtime",
      "--test",
      "core",
      "--test",
      "model_accounting",
      "--test",
      "telemetry",
    ]);
    await cargo("recording-and-export", [
      "-p",
      "openworker-observability",
      "-p",
      "openworker-telemetry",
      "--all-features",
    ]);
    await check("management-and-safety-contracts", "node", [
      "--test",
      "deploy/resilience/contract.test.mjs",
      "deploy/service/local-drain.test.mjs",
      "harness/service-ui/test/observability.test.mjs",
    ]);
  } else if (level === "postgres") {
    const runner = process.env.OW_RESILIENCE_RUNNER_IMAGE;
    assert.match(
      runner ?? "",
      /^sha256:[a-f0-9]{64}$/,
      "Set current-source OW_RESILIENCE_RUNNER_IMAGE for process-manager qualification",
    );
    const id = randomUUID().replaceAll("-", "").slice(0, 12),
      name = "ow-resilience-pg-" + id,
      label = "openworker.resilience",
      password = randomBytes(24).toString("hex");
    const secret = join(root, "password"),
      envFile = join(root, "linux-env");
    const image =
      "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685";
    await docker("image", "inspect", image);
    await writeFile(secret, password, { mode: 0o600, flag: "wx" });
    let intended = false;
    try {
      await writeFile(
        join(root, "postgres-intent.json"),
        JSON.stringify({ name, label, id, image }),
        { mode: 0o600, flag: "wx" },
      );
      intended = true;
      await docker(
        "run",
        "--pull=never",
        "-d",
        "--rm",
        "--name",
        name,
        "--label",
        `${label}=${id}`,
        "--memory",
        "512m",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,size=512m",
        "-p",
        "127.0.0.1::5432",
        "-e",
        "POSTGRES_USER=openworker",
        "-e",
        "POSTGRES_DB=execution",
        "-e",
        "POSTGRES_PASSWORD_FILE=/run/secrets/password",
        "--mount",
        `type=bind,source=${secret},target=/run/secrets/password,readonly`,
        image,
      );
      const inspect = JSON.parse(await docker("inspect", name))[0];
      assert.equal(inspect.Config.Labels[label], id);
      let ready = false;
      for (let n = 0; n < 120; n++) {
        try {
          await docker(
            "exec",
            name,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            "openworker",
            "-d",
            "execution",
          );
          ready = true;
          break;
        } catch {}
        await new Promise((r) => setTimeout(r, 250));
      }
      assert(ready);
      await docker(
        "exec",
        name,
        "createdb",
        "-U",
        "openworker",
        "observability",
      );
      const port = inspect.NetworkSettings.Ports["5432/tcp"][0];
      assert.equal(port.HostIp, "127.0.0.1");
      const vars = {
        OW_PG_TEST_URL: `postgresql://openworker:${password}@127.0.0.1:${port.HostPort}/execution`,
        OW_EXECUTION_TEST_URL: `postgresql://openworker:${password}@127.0.0.1:${port.HostPort}/execution`,
        OW_OBSERVABILITY_TEST_URL: `postgresql://openworker:${password}@127.0.0.1:${port.HostPort}/observability`,
      };
      await cargo(
        "postgres-submissions",
        [
          "-p",
          "openworker-store-postgres",
          "--test",
          "submissions",
          "--test",
          "environments",
          "--",
          "--ignored",
          "--test-threads=1",
        ],
        vars,
      );
      await cargo(
        "postgres-recording-outage",
        [
          "-p",
          "openworker-server-reference",
          "--test",
          "recording",
          "--",
          "--ignored",
          "--test-threads=1",
        ],
        vars,
      );
      await cargo(
        "postgres-authorized-recording",
        [
          "-p",
          "openworker-server-reference",
          "--test",
          "recording_queries",
          "--",
          "--ignored",
          "--test-threads=1",
        ],
        vars,
      );
      if (
        process.platform === "linux" &&
        !process.env.OW_RESILIENCE_LINUX_IMAGE
      ) {
        await cargo(
          "postgres-worker-faults",
          [
            "-p",
            "openworker-store-postgres",
            "--features",
            "test-support",
            "--test",
            "worker_recovery",
            "--",
            "--ignored",
            "--test-threads=1",
            "--skip",
            "mixed_versions_keep_native_identity_pinned_and_old_pool_can_resume",
            "--skip",
            "bounded_load_matrix",
          ],
          vars,
        );
      } else {
        const linux = process.env.OW_RESILIENCE_LINUX_IMAGE;
        assert.match(
          linux ?? "",
          /^sha256:[a-f0-9]{64}$/,
          "macOS requires current-source OW_RESILIENCE_LINUX_IMAGE; Linux faults may not silently skip",
        );
        await docker("image", "inspect", linux);
        await check(
          "postgres-linux-worker-faults",
          "node",
          ["deploy/resilience/postgres-workers.mjs", root, linux, image],
          {},
          1800000,
        );
        const isolated = JSON.parse(
          await readFile(join(root, "worker-cases/report.json")),
        );
        assert(
          isolated.passed &&
            isolated.cleanup.allOwnedContainersRemoved &&
            isolated.cleanup.allGeneratedSecretsRemoved,
        );
        report.cleanup.linuxFixtureRemoved = true;
      }
      await check("docker-process-manager", "node", [
        "deploy/reference/qualify-process-continuity.mjs",
        runner,
      ]);
    } finally {
      if (intended) {
        const v = await inspectOptional(name);
        if (v) {
          assert.equal(v.Config.Labels[label], id);
          await docker("rm", "-f", v.Id);
        }
        report.cleanup.ownedPostgresRemoved = true;
      }
      await Promise.all(
        [secret, envFile].map((p) =>
          unlink(p).catch((e) => {
            if (e.code !== "ENOENT") throw e;
          }),
        ),
      );
      report.cleanup.generatedSecretsRemoved = true;
    }
  } else {
    assert.equal(
      extra.length,
      2,
      "cluster requires SERVICE_STATE and exact service ID",
    );
    await check(
      "kubernetes-openshell",
      "node",
      ["deploy/resilience/cluster.mjs", resolve(extra[0]), extra[1], root],
      { NODE_EXTRA_CA_CERTS: join(resolve(extra[0]), "input/api-ca.crt") },
      1800000,
    );
    report.cluster = JSON.parse(
      await readFile(join(root, "cluster-report.json")),
    );
    assert(report.cluster.passed);
    await check(
      "owned-service-cleanup",
      "node",
      [
        "deploy/service/kubernetes-local.mjs",
        "destroy",
        resolve(extra[0]),
        extra[1],
      ],
      {},
      300000,
    );
    report.cleanup.ownedServiceNamespaceRemoved = true;
  }
  report.passed = true;
} finally {
  report.finished = new Date().toISOString();
  await writeFile(
    join(root, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
    { mode: 0o600, flag: "wx" },
  );
  console.log(JSON.stringify(report));
}
