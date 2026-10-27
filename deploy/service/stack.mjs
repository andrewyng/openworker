// Local container installer. No cloud calls, Docker socket mounts or kubecontext changes.
import { spawn } from "node:child_process";
import {
  readFile,
  writeFile,
  mkdir,
  copyFile,
  chmod,
  stat,
} from "node:fs/promises";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
export async function run(
  bin,
  args,
  { input, timeout = 240000, inherit = false } = {},
) {
  return new Promise((ok, no) => {
    const p = spawn(bin, args, {
      stdio: inherit
        ? ["pipe", "inherit", "inherit"]
        : ["pipe", "pipe", "pipe"],
    });
    let out = "",
      err = "";
    if (!inherit) {
      p.stdout.on("data", (b) => {
        if (out.length < 2000000) out += b;
      });
      p.stderr.on("data", (b) => {
        if (err.length < 4000) err += b;
      });
    }
    p.on("error", () => no(Error(`${bin} could not start`)));
    p.stdin.on("error", () => {});
    p.stdin.end(input);
    const timer = setTimeout(() => p.kill("SIGTERM"), timeout);
    p.on("close", (code) => {
      clearTimeout(timer);
      code === 0
        ? ok(out)
        : no(
            Error(
              `${bin} failed (${code}); inspect this deployment's logs. Output withheld to protect credentials.`,
            ),
          );
    });
  });
}
async function privateWrite(path, value) {
  await writeFile(path, value, { mode: 0o600, flag: "wx" });
}
async function certificate(input, name, cn, san) {
  const ca = join(input, `${name}-ca`),
    cert = join(input, name);
  await run("openssl", [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    ca + ".key",
    "-out",
    ca + ".crt",
    "-days",
    "365",
    "-subj",
    `/CN=${cn} development CA`,
  ]);
  await run("openssl", [
    "req",
    "-new",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    cert + ".key",
    "-out",
    cert + ".csr",
    "-subj",
    `/CN=${cn}`,
  ]);
  await privateWrite(
    cert + ".ext",
    `subjectAltName=${san}\nextendedKeyUsage=serverAuth\n`,
  );
  await run("openssl", [
    "x509",
    "-req",
    "-in",
    cert + ".csr",
    "-CA",
    ca + ".crt",
    "-CAkey",
    ca + ".key",
    "-CAcreateserial",
    "-out",
    cert + ".crt",
    "-days",
    "30",
    "-extfile",
    cert + ".ext",
  ]);
  for (const f of [ca + ".key", cert + ".key"]) await chmod(f, 0o600);
}
export async function init(root, connectionFile, image, port = 58443) {
  root = resolve(root);
  if (/[\r\n$]/.test(root))
    throw Error("State path cannot contain newlines or dollar signs");
  if (!Number.isInteger(port) || port < 1024 || port > 65535)
    throw Error("Invalid local port");
  const c = JSON.parse(await readFile(resolve(connectionFile), "utf8"));
  const gateway = new URL(c.openshell.endpoint);
  if (
    gateway.protocol !== "https:" ||
    gateway.username ||
    gateway.password ||
    gateway.search ||
    gateway.hash
  )
    throw Error("OpenShell requires a verified HTTPS endpoint");
  if (!c.openshell.image.includes("@sha256:"))
    throw Error("Runner image must be pinned by digest");
  const imageId = (
    await run("docker", ["image", "inspect", image, "--format", "{{.Id}}"])
  ).trim();
  const id = "ows-" + randomUUID().replaceAll("-", "").slice(0, 12);
  await mkdir(root, { mode: 0o700 });
  await mkdir(join(root, "input"), { mode: 0o700 });
  const input = join(root, "input");
  await mkdir(join(input, "openshell"), { mode: 0o700 });
  for (const name of ["ca.crt", "tls.crt", "tls.key"]) {
    await copyFile(
      join(resolve(c.openshell.certificates), name),
      join(input, "openshell", name),
    );
    await chmod(join(input, "openshell", name), 0o600);
  }
  await copyFile(resolve(c.runner), join(input, "runner"));
  await chmod(join(input, "runner"), 0o500);
  const password = randomBytes(32).toString("hex"),
    admin = randomBytes(32).toString("hex");
  await privateWrite(join(input, "pg-password"), password);
  await privateWrite(join(input, "admin-token"), admin);
  await certificate(input, "pg", "postgres", "DNS:postgres");
  await certificate(input, "api", "localhost", "DNS:localhost,IP:127.0.0.1");
  await privateWrite(
    join(input, "database-url"),
    `postgresql://openworker:${password}@postgres:5432/openworker`,
  );
  const scripted = c.scripted === true;
  if (!scripted && (!c.model || !c.modelKeyFile))
    throw Error(
      "Live setup requires explicit model and modelKeyFile; use scripted:true for qualification",
    );
  const provider = c.modelProvider ?? "openai";
  if (!["openai", "anthropic"].includes(provider))
    throw Error("Unsupported local model provider");
  if (!scripted) {
    await copyFile(resolve(c.modelKeyFile), join(input, `model-${provider}`));
    await chmod(join(input, `model-${provider}`), 0o600);
  }
  const config = {
    service: { id, namespace: "local", agent: "installed-tools" },
    database_env: "OW_PG_URL",
    schema: "ow_service",
    database_loopback_plaintext: false,
    database_ca: "/run/service/pg-ca.crt",
    state_root: "/state",
    bundle: "/opt/openworker/bundle",
    runner: "/runner/openworker",
    model: scripted ? "fixture" : c.model,
    scripted,
    model_provider: provider,
    model_connections: scripted
      ? []
      : [
          {
            id: "primary",
            provider,
            identity: `${id}-model`,
            key_env:
              provider === "openai" ? "OPENAI_API_KEY" : "ANTHROPIC_API_KEY",
          },
        ],
    api_token_file: "/bootstrap/admin-token",
    actor: "local-admin",
    openshell: {
      identity: id,
      endpoint: gateway.toString().replace(/\/$/, ""),
      certificates: "/openshell",
      tls_server_name: c.openshell.tls_server_name,
      workspace: c.openshell.workspace ?? "default",
      image: c.openshell.image,
    },
  };
  await privateWrite(
    join(input, "config.json"),
    JSON.stringify(config, null, 2),
  );
  await privateWrite(
    join(input, "nginx.conf"),
    `pid /tmp/nginx.pid;\nerror_log /dev/stderr warn;\nevents {worker_connections 256;}\nhttp {access_log off;client_body_temp_path /tmp/body;proxy_temp_path /tmp/proxy;fastcgi_temp_path /tmp/fastcgi;uwsgi_temp_path /tmp/uwsgi;scgi_temp_path /tmp/scgi;\nserver {listen 8443 ssl;ssl_certificate /tls/tls.crt;ssl_certificate_key /tls/tls.key;ssl_protocols TLSv1.2 TLSv1.3;client_max_body_size 160k;\nlocation / {proxy_pass http://127.0.0.1:8080;proxy_http_version 1.1;proxy_set_header Connection "";proxy_buffering off;proxy_read_timeout 3600s;}}}\n`,
  );
  const manifest = {
    id,
    image,
    imageId,
    port,
    url: `https://localhost:${port}`,
    root,
    scripted,
  };
  await privateWrite(
    join(root, "deployment.json"),
    JSON.stringify(manifest, null, 2),
  );
  await privateWrite(
    join(root, "cli.json"),
    JSON.stringify(
      {
        selected: "local",
        contexts: {
          local: {
            url: manifest.url,
            namespace: "local",
            tokenFile: join(input, "admin-token"),
            tokenEnv: null,
            caFile: join(input, "api-ca.crt"),
            serviceId: id,
          },
        },
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({
      id,
      url: manifest.url,
      cliConfig: join(root, "cli.json"),
    }),
  );
  return manifest;
}
export async function load(root) {
  root = resolve(root);
  const m = JSON.parse(await readFile(join(root, "deployment.json"), "utf8"));
  if (m.root !== root || !/^ows-[a-f0-9]{12}$/.test(m.id))
    throw Error("Deployment identity/path mismatch");
  return m;
}
export async function compose(m, args, opts = {}) {
  const current = (
    await run("docker", ["image", "inspect", m.image, "--format", "{{.Id}}"])
  ).trim();
  if (current !== m.imageId)
    throw Error(
      "Service image changed; create a new deployment or perform an explicit upgrade",
    );
  // env file contains references only; no passwords or access tokens.
  const path = join(m.root, "compose.env");
  await writeFile(
    path,
    `SERVICE_IMAGE=${m.imageId}\nSTATE_DIR=${m.root}\nAPI_PORT=${m.port}\n`,
    { mode: 0o600 },
  );
  return run(
    "docker",
    [
      "compose",
      "--project-name",
      m.id,
      "--env-file",
      path,
      "-f",
      join(here, "compose.yaml"),
      ...args,
    ],
    opts,
  );
}
async function main() {
  const [cmd, root, arg, image, port] = process.argv.slice(2);
  if (!cmd || !root)
    throw Error(
      "Usage: node deploy/service/stack.mjs init STATE CONNECTION.json IMAGE [PORT] | up/down/status/destroy STATE [DEPLOYMENT_ID]",
    );
  if (cmd === "init") {
    await init(root, arg, image, port ? Number(port) : 58443);
    return;
  }
  const m = await load(root);
  if (m.destroyed || (m.destroying && cmd !== "destroy"))
    throw Error(
      "Deployment was destroyed or destruction started; use a new state directory and identity",
    );
  if (cmd === "up") {
    await compose(m, ["up", "-d", "--wait"], { inherit: true });
    console.log(
      JSON.stringify({ url: m.url, cliConfig: join(m.root, "cli.json") }),
    );
  } else if (cmd === "down") await compose(m, ["down"], { inherit: true });
  else if (cmd === "status") console.log(await compose(m, ["ps", "--all"]));
  else if (cmd === "restart-api")
    await compose(m, ["restart", "api"], { inherit: true });
  else if (cmd === "destroy") {
    if (arg !== m.id) throw Error("destroy requires the exact deployment ID");
    if (!m.destroying) {
      const remaining = (
        await compose(m, [
          "exec",
          "-T",
          "postgres",
          "psql",
          "-U",
          "openworker",
          "-d",
          "openworker",
          "-Atc",
          "SELECT count(*) FROM ow_service.service_environment_owners o JOIN ow_service.environments e ON e.id=o.environment_id WHERE e.data->>'phase'<>'deleted'",
        ])
      ).trim();
      if (remaining !== "0")
        throw Error(
          "Close sessions and delete environments with ows before destroying the database. down retains data.",
        );
      await writeFile(
        join(m.root, "deployment.json"),
        JSON.stringify({ ...m, destroying: true }, null, 2),
        { mode: 0o600 },
      );
    }
    await compose(m, ["down", "--volumes", "--remove-orphans"], {
      inherit: true,
    });
    await writeFile(
      join(m.root, "deployment.json"),
      JSON.stringify({ ...m, destroyed: true }, null, 2),
      { mode: 0o600 },
    );
    console.log(
      "Deployment containers and volumes removed; local credential files retained for operator disposal.",
    );
  } else throw Error("Unknown command");
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  main().catch((e) => {
    console.error(e.message);
    process.exitCode = 1;
  });
