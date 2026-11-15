// Node 20+, no npm dependencies. Owns native child handles, never arbitrary PIDs.
import { mkdir, readFile, writeFile, open, stat } from "node:fs/promises";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import net from "node:net";
import { write as writeOperations } from "./operations.mjs";
const here = dirname(fileURLToPath(import.meta.url));
export const repo = resolve(here, "../..");
export const workerLifecycle = Object.freeze({
  drainSeconds: 300,
  cleanupSeconds: 45,
  marginSeconds: 15,
});
export const stopBudgetMs = (role) =>
  role.startsWith("worker-")
    ? (workerLifecycle.drainSeconds +
        workerLifecycle.cleanupSeconds +
        workerLifecycle.marginSeconds) *
      1000
    : 35000;
const secret = () => randomBytes(32).toString("hex");
export const json = async (p) => JSON.parse(await readFile(p, "utf8"));
export const delay = (ms) => new Promise((r) => setTimeout(r, ms));
export async function command(program, args, options = {}) {
  const child = spawn(program, args, {
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  let out = "",
    error = "";
  child.stdout?.on("data", (b) => {
    if (out.length < 262144) out += b;
  });
  child.stderr?.on("data", (b) => {
    if (error.length < 262144) error += b;
  });
  const [code] = await once(child, "close");
  if (code !== 0)
    throw Error(
      `${program} failed (${code}); inspect the private service logs. ${options.safeOutput ? error : ""}`,
    );
  return out;
}
export async function init(root, connection, observability = true, offset = 0) {
  root = resolve(root);
  if (/[\r\n$#]/.test(root)) throw Error("Choose a simple state path");
  if (!Number.isInteger(offset) || offset < 0 || offset > 7000)
    throw Error("Port offset must be 0–7000");
  const gateway = await json(resolve(connection));
  if (!gateway.runner || !gateway.openshell)
    throw Error("Connection file needs runner and openshell");
  await mkdir(root, { mode: 0o700 }); // Refuse to overwrite an existing deployment.
  await mkdir(join(root, "private"), { mode: 0o700 });
  await mkdir(join(root, "logs"), { mode: 0o700 });
  const ports = {
    pg: 55461,
    s3: 58333,
    otlp: 54318,
    grafana: 53000,
    apiA: 58181,
    apiB: 58182,
    console: 54340,
  };
  for (const name of Object.keys(ports)) ports[name] += offset;
  const id = randomUUID().replaceAll("-", "");
  const manifest = {
    version: 1,
    project: `owref-${id.slice(0, 12)}`,
    id,
    ports,
    observability,
    observer_request_id: "local-console-observer",
  };
  const write = (name, data) =>
    writeFile(join(root, name), data, { mode: 0o600, flag: "wx" });
  const pg = secret(),
    access = secret().slice(0, 20),
    key = secret();
  await write("private/postgres-password", pg);
  await write("private/api-token", secret());
  await write("private/observer-token", secret());
  await write(
    "private/observer-spec.json",
    JSON.stringify({
      principal: "console-observer",
      role: "observer",
      scope: { kind: "all" },
    }),
  );
  await write("private/console-key", secret());
  await write("private/grafana-password", secret());
  await write(
    "private/s3.json",
    JSON.stringify({
      identities: [
        {
          name: "local-app",
          credentials: [{ accessKey: access, secretKey: key }],
          actions: [
            "Read:openworker-artifacts",
            "Write:openworker-artifacts",
            "List:openworker-artifacts",
          ],
        },
      ],
    }),
  );
  await write(
    "private/runtime-env.json",
    JSON.stringify({
      OW_SERVER_DATABASE_URL: `postgresql://openworker:${pg}@127.0.0.1:${ports.pg}/openworker`,
      AWS_ACCESS_KEY_ID: access,
      AWS_SECRET_ACCESS_KEY: key,
    }),
  );
  const config = {
    database_env: "OW_SERVER_DATABASE_URL",
    schema: "ow_reference",
    database_loopback_plaintext: true,
    state_root: join(root, "controllers"),
    bundle: join(repo, "harness/examples/assistant"),
    runner: resolve(gateway.runner),
    model: "fixture",
    scripted: true,
    api_token_file: join(root, "private/api-token"),
    actor: "local-operator",
    openshell: gateway.openshell,
    artifacts: {
      kind: "s3",
      deployment: `local-${id}`,
      bucket: "openworker-artifacts",
      region: "us-east-1",
      endpoint: `http://127.0.0.1:${ports.s3}`,
    },
    telemetry: { endpoint: `http://127.0.0.1:${ports.otlp}`, deployment: id },
    operational_metrics: {},
  };
  if (!observability) {
    delete config.telemetry;
    delete config.operational_metrics;
  }
  await writeOperations(root, { deployment: id, expectedSlots: 2 });
  await write("reference.json", JSON.stringify(config, null, 2));
  await write(
    "console.json",
    JSON.stringify(
      {
        port: ports.console,
        apis: [ports.apiA, ports.apiB].map((p) => `http://127.0.0.1:${p}`),
        api_token_file: join(root, "private/observer-token"),
        access_key_file: join(root, "private/console-key"),
        grafana_url: observability
          ? `http://127.0.0.1:${ports.grafana}/d/openworker-harness`
          : null,
      },
      null,
      2,
    ),
  );
  await write(
    "compose.env",
    `STATE_DIR=${root}\nPG_PORT=${ports.pg}\nS3_PORT=${ports.s3}\nOTLP_PORT=${ports.otlp}\nGRAFANA_PORT=${ports.grafana}\n`,
  );
  await write("deployment.json", JSON.stringify(manifest, null, 2));
  return manifest;
}
export async function load(root) {
  root = resolve(root);
  const m = await json(join(root, "deployment.json"));
  if (m.version !== 1 || !/^owref-[a-f0-9]{12}$/.test(m.project))
    throw Error("Not a reference deployment");
  if ((await stat(join(root, "private"))).mode & 0o077)
    throw Error("Private state directory must be mode 0700");
  return { ...m, root };
}
export async function compose(m, args) {
  const endpoint =
    process.env.DOCKER_HOST ||
    (
      await command("docker", [
        "context",
        "inspect",
        "--format",
        "{{.Endpoints.docker.Host}}",
      ])
    ).trim();
  if (!endpoint.startsWith("unix://"))
    throw Error(
      "This reference requires a local Unix-socket Docker daemon; remote contexts are out of scope",
    );
  return command(
    "docker",
    [
      "compose",
      "--project-name",
      m.project,
      "--env-file",
      join(m.root, "compose.env"),
      "-f",
      join(here, "compose.yaml"),
      ...(m.observability ? ["--profile", "observability"] : []),
      ...args,
    ],
    { safeOutput: true },
  );
}
export async function up(m) {
  await compose(m, ["up", "-d", "--wait", "--wait-timeout", "120"]);
  await until(
    async () =>
      (
        await fetch(`http://127.0.0.1:${m.ports.s3}/openworker-artifacts`, {
          signal: AbortSignal.timeout(2000),
        })
      ).status === 403,
  );
  if (m.observability)
    await until(
      async () =>
        (
          await fetch(`http://127.0.0.1:${m.ports.grafana}/api/health`, {
            signal: AbortSignal.timeout(2000),
          })
        ).ok,
    );
}
export async function until(fn, ms = 60000, signal) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    signal?.throwIfAborted();
    try {
      const r = await fn();
      if (r) {
        signal?.throwIfAborted();
        return r;
      }
    } catch {}
    await delay(300);
  }
  throw Error("Readiness deadline exceeded");
}
async function available(port) {
  const s = net.createServer();
  s.listen(port, "127.0.0.1");
  await once(s, "listening");
  await new Promise((r) => s.close(r));
}
async function provisionAuthorization(m, bin, config, env, upgrade = false) {
  if (upgrade) {
    // Explicit upgrade only: preserve all existing credentials, including revoked keys.
    for (const [name, value] of [
      ["observer-token", secret()],
      [
        "observer-spec.json",
        JSON.stringify({
          principal: "console-observer",
          role: "observer",
          scope: { kind: "all" },
        }),
      ],
    ]) {
      try {
        await writeFile(join(m.root, "private", name), value, {
          mode: 0o600,
          flag: "wx",
        });
      } catch (e) {
        if (e.code !== "EEXIST") throw e;
      }
    }
  }
  await command(bin, ["--config", config, "auth-bootstrap"], { env });
  const observer = JSON.parse(
    await command(
      bin,
      [
        "--config",
        config,
        "auth-issue",
        "--spec",
        join(m.root, "private/observer-spec.json"),
        "--token-file",
        join(m.root, "private/observer-token"),
        "--request-id",
        m.observer_request_id || "local-console-observer",
      ],
      { env },
    ),
  );
  if (observer.revoked)
    throw Error("Console credential revoked; explicitly issue a replacement");
  if (upgrade) {
    const path = join(m.root, "console.json"),
      c = await json(path);
    c.api_token_file = join(m.root, "private/observer-token");
    await writeFile(path, JSON.stringify(c, null, 2), { mode: 0o600 });
  }
}
export async function launch(m, signal) {
  const bin =
    process.env.OW_SERVER_BINARY ||
    join(repo, "harness/target/debug/openworker-server-reference");
  const env = {
    ...process.env,
    ...(await json(join(m.root, "private/runtime-env.json"))),
  };
  // Never forward ambient cloud credential/config overrides into this reference.
  for (const k of Object.keys(env))
    if (
      k.startsWith("AWS_") &&
      !["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"].includes(k)
    )
      delete env[k];
  const config = join(m.root, "reference.json"),
    children = new Map();
  const token = (
    await readFile(join(m.root, "private/api-token"), "utf8")
  ).trim();
  for (const p of [m.ports.apiA, m.ports.apiB, m.ports.console])
    await available(p);
  await command(bin, ["--config", config, "migrate"], { env });
  await provisionAuthorization(m, bin, config, env);
  async function start(role) {
    signal?.throwIfAborted();
    if (!["api-a", "api-b", "worker-a", "worker-b", "console"].includes(role))
      throw Error("Unknown native role");
    if (children.has(role)) throw Error("Role already running");
    const api = role.startsWith("api");
    const args =
      role === "console"
        ? [
            join(repo, "harness/console/server.mjs"),
            join(m.root, "console.json"),
          ]
        : [
            "--config",
            config,
            api ? "api" : "worker",
            "--node",
            role,
            ...(api
              ? [
                  "--listen",
                  `127.0.0.1:${role === "api-a" ? m.ports.apiA : m.ports.apiB}`,
                ]
              : [
                  "--slots",
                  "1",
                  "--drain-timeout-seconds",
                  String(workerLifecycle.drainSeconds),
                  "--shutdown-timeout-seconds",
                  String(workerLifecycle.cleanupSeconds),
                ]),
          ];
    const log = await open(join(m.root, "logs", `${role}.log`), "a", 0o600);
    if (signal?.aborted) {
      await log.close();
      signal.throwIfAborted();
    }
    const childEnv =
      role === "console"
        ? Object.fromEntries(
            ["PATH", "LANG", "LC_ALL", "TZ"]
              .filter((k) => process.env[k])
              .map((k) => [k, process.env[k]]),
          )
        : { ...env };
    if (!api)
      for (const k of Object.keys(childEnv))
        if (k.startsWith("AWS_")) delete childEnv[k];
    const child = spawn(role === "console" ? process.execPath : bin, args, {
      env: childEnv,
      stdio: ["ignore", log.fd, log.fd],
    });
    await log.close();
    children.set(role, child);
    child.on("error", () => {});
    if (api)
      await until(
        async () =>
          (
            await fetch(
              `http://127.0.0.1:${role === "api-a" ? m.ports.apiA : m.ports.apiB}/readyz`,
              {
                headers: { Authorization: `Bearer ${token}` },
                signal: AbortSignal.timeout(2000),
              },
            )
          ).ok,
        20000,
        signal,
      );
    return child;
  }
  async function stop(role, signal = "SIGTERM") {
    const child = children.get(role);
    if (!child) return;
    children.delete(role);
    if (child.exitCode !== null || child.signalCode) return;
    const done = once(child, "exit");
    child.kill(signal);
    const timer = setTimeout(() => child.kill("SIGKILL"), stopBudgetMs(role));
    try {
      await done;
    } finally {
      clearTimeout(timer);
    }
  }
  async function close() {
    await Promise.all([...children.keys()].map((r) => stop(r)));
  }
  try {
    for (const role of ["api-a", "api-b", "worker-a", "worker-b", "console"])
      await start(role);
    await until(
      async () => {
        for (const p of [m.ports.apiA, m.ports.apiB])
          if (
            !(
              await fetch(`http://127.0.0.1:${p}/readyz`, {
                headers: { Authorization: `Bearer ${token}` },
                signal: AbortSignal.timeout(2000),
              })
            ).ok
          )
            return false;
        return true;
      },
      60000,
      signal,
    );
    await until(
      async () =>
        (
          await fetch(`http://127.0.0.1:${m.ports.console}/`, {
            signal: AbortSignal.timeout(2000),
          })
        ).ok,
      60000,
      signal,
    );
  } catch (e) {
    await close();
    throw e;
  }
  return { children, start, stop, close, token, env, bin, config };
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [action, root, connection] = process.argv.slice(2);
  if (
    !root ||
    !["init", "up", "run", "down", "status", "authorize"].includes(action)
  )
    throw Error(
      "Usage: node stack.mjs init STATE CONNECTION.json | up/run/down/status/authorize STATE",
    );
  if (action === "init") {
    const n = process.argv.indexOf("--port-offset");
    await init(
      root,
      connection,
      !process.argv.includes("--no-observability"),
      n < 0 ? 0 : Number(process.argv[n + 1]),
    );
    console.log("Private configuration created. Run up, then run.");
  } else {
    const m = await load(root);
    if (action === "authorize") {
      const bin =
          process.env.OW_SERVER_BINARY ||
          join(repo, "harness/target/debug/openworker-server-reference"),
        config = join(m.root, "reference.json"),
        env = {
          ...process.env,
          ...(await json(join(m.root, "private/runtime-env.json"))),
        };
      await command(bin, ["--config", config, "migrate"], { env });
      await provisionAuthorization(m, bin, config, env, true);
      console.log(
        "Authorization provisioned; console uses an observer. No saved sessions or credentials deleted.",
      );
    }
    if (action === "up") {
      await up(m);
      console.log("Backing services started.");
    }
    if (action === "down") {
      await compose(m, ["down"]);
      console.log(
        "Backing services stopped; named volumes and private state retained.",
      );
    }
    if (action === "status") console.log(await compose(m, ["ps"]));
    if (action === "run") {
      const startup = new AbortController();
      const interrupted = Promise.race([
        once(process, "SIGINT"),
        once(process, "SIGTERM"),
      ]);
      interrupted.then(() => startup.abort());
      const running = await launch(m, startup.signal);
      console.log(
        `Console: http://127.0.0.1:${m.ports.console}/\nAccess key file: ${join(m.root, "private/console-key")}${m.observability ? `\nGrafana: http://127.0.0.1:${m.ports.grafana}/ (admin; password in private/grafana-password)` : ""}\nCtrl-C stops native children; backing services/data remain.`,
      );
      await interrupted;
      await running.close();
    }
  }
}
