// Local-only recovery drill. Source Kubernetes is read-only; all writes target
// two new Docker projects with internal networks, no gateway/model credentials.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, chmod, copyFile } from "node:fs/promises";
import { join } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import https from "node:https";
import {
  repo,
  root as sourceRoot,
  settings,
  namespace,
} from "../helm/local.mjs";
import { capture, verify, hash, privateWrite, MAX_DUMP } from "./backup.mjs";
const pgImage =
  "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685";
const s3Image =
  "chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d";
const nginx =
  "nginx@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236";
let diagnostics;
export function command(
  program,
  args,
  { input, limit = MAX_DUMP, timeout = 120000 } = {},
) {
  return new Promise((resolve, reject) => {
    const p = spawn(program, args, { stdio: ["pipe", "pipe", "pipe"] }),
      chunks = [];
    let size = 0,
      failed = false,
      stderr = "";
    const fail = (reason) => {
      if (!failed) {
        failed = true;
        p.kill("SIGKILL");
        reject(Error(reason));
      }
    };
    p.on("error", () => fail(program + " unavailable"));
    p.stderr.on("data", (b) => {
      if (stderr.length < 16384) stderr += b.toString();
    });
    p.stdin.on("error", () => {});
    p.stdout.on("data", (b) => {
      size += b.length;
      if (size > limit) fail(program + " output limit");
      else chunks.push(b);
    });
    const timer = setTimeout(
      () =>
        fail(
          program + " timed out; inspect original operation before retrying",
        ),
      timeout,
    );
    p.on("close", async (code) => {
      clearTimeout(timer);
      if (failed) return;
      if (code === 0) resolve(Buffer.concat(chunks));
      else {
        const path =
          diagnostics && join(diagnostics, "error-" + randomUUID() + ".log");
        if (path) await privateWrite(path, stderr);
        const error = Error(
          program +
            " " +
            args[0] +
            " failed; private diagnostic: " +
            (path || "unavailable"),
        );
        error.diagnostic = path;
        reject(error);
      }
    });
    p.stdin.end(input);
  });
}
const json = async (p) => JSON.parse(await readFile(p, "utf8")),
  secret = () => randomBytes(32).toString("hex");
const wait = async (fn) => {
  for (let i = 0; i < 80; i++) {
    try {
      const x = await fn();
      if (x) return x;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw Error("Readiness deadline");
};
const ca = await readFile(join(sourceRoot, "private/api.ca.crt"));
const admin = (
  await readFile(join(sourceRoot, "private/admin"), "utf8")
).trim();
const m = await settings();
assert.equal(m.context, "kind-ow-server-20261011");
const values = await json(join(sourceRoot, "values.json"));
assert.equal(values.config.schema, "ow_helm");
const endpoint = new URL(values.config.artifacts.endpoint);
assert.equal(endpoint.hostname, "objects.ow-helm-eval.svc.cluster.local");
assert.equal(endpoint.port, "8443");
const dockerEndpoint = (
  await command("docker", [
    "context",
    "inspect",
    "--format",
    "{{.Endpoints.docker.Host}}",
  ])
)
  .toString()
  .trim();
assert.ok(
  dockerEndpoint.startsWith("unix://") && !process.env.DOCKER_HOST,
  "Local default Docker daemon required",
);
const id = randomUUID().replaceAll("-", "").slice(0, 12),
  root = join(repo, ".ow-recovery", id),
  checks = [];
await mkdir(join(repo, ".ow-recovery"), { recursive: true, mode: 0o700 });
await mkdir(root, { mode: 0o700 });
diagnostics = root;
await mkdir(join(root, "bin"), { mode: 0o700 });
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
console.log("Private recovery drill: " + root);
for (const [binary, remote] of [
  ["server", "openworker-server-reference"],
  ["fixture", "pg-recovery-fixture"],
]) {
  await command("docker", [
    "cp",
    `ow-harness-controls-runner-20261011:/build/debug/${remote}`,
    join(root, "bin", binary),
  ]);
  await chmod(join(root, "bin", binary), 0o755);
}
const kargs = [
  "--kubeconfig",
  m.kubeconfig,
  "--context",
  m.context,
  "-n",
  namespace,
];
const originalDump = await command("kubectl", [
  ...kargs,
  "exec",
  "deployment/postgres",
  "--",
  "pg_dump",
  "-U",
  "openworker",
  "-d",
  "openworker",
  "--format=custom",
  "--no-owner",
  "--no-privileges",
  "--schema=ow_helm",
  "--lock-wait-timeout=10s",
]);
await privateWrite(join(root, "original.dump"), originalDump);
const revoked = secret();
await privateWrite(join(root, "admin"), admin);
await privateWrite(join(root, "revoked"), revoked);
const projects = [];
let forward;
function compose(s, args, input) {
  return command(
    "docker",
    [
      "compose",
      "--project-name",
      s.project,
      "-f",
      join(s.dir, "compose.json"),
      ...args,
    ],
    { input },
  );
}
const port = async (s, service, p) =>
  Number(
    (await compose(s, ["port", service, String(p)]))
      .toString()
      .trim()
      .split(":")
      .at(-1),
  );
const sql = async (s, query) =>
  (
    await compose(s, [
      "exec",
      "-T",
      "postgres",
      "psql",
      "-X",
      "-U",
      "openworker",
      "-d",
      "openworker",
      "-v",
      "ON_ERROR_STOP=1",
      "-Atc",
      query,
    ])
  )
    .toString()
    .trim();
async function fresh(which) {
  const dir = join(root, which),
    project = `owrestore-${id}-${which}`,
    s = { dir, project };
  await mkdir(dir, { mode: 0o700 });
  await mkdir(join(dir, "private"), { mode: 0o700 });
  for (const f of [
    "pg.ca.crt",
    "pg.crt",
    "pg.key",
    "objects.ca.crt",
    "objects.crt",
    "objects.key",
    "api.ca.crt",
    "api.crt",
    "api.key",
  ]) {
    await copyFile(join(sourceRoot, "private", f), join(dir, "private", f));
    await chmod(join(dir, "private", f), 0o600);
  }
  const pg = secret(),
    s3 = secret();
  s.s3 = s3;
  await privateWrite(join(dir, "private/pg-password"), pg);
  await privateWrite(
    join(dir, "private/s3.json"),
    JSON.stringify({
      identities: [
        {
          name: "restored",
          credentials: [{ accessKey: "recovery", secretKey: s3 }],
          actions: [
            "Read:openworker-artifacts",
            "Write:openworker-artifacts",
            "List:openworker-artifacts",
          ],
        },
      ],
    }),
  );
  await privateWrite(
    join(dir, "private/api.env"),
    `OW_SERVER_DATABASE_URL=postgresql://openworker:${pg}@postgres:5432/openworker\nAWS_ACCESS_KEY_ID=recovery\nAWS_SECRET_ACCESS_KEY=${s3}\n`,
  );
  await privateWrite(
    join(dir, "private/pg_hba.conf"),
    "local all all trust\nhostssl all all all scram-sha-256\nhostnossl all all all reject\n",
  );
  const config = {
    ...values.config,
    recovery_read_only: true,
    state_root: "/state",
    bundle: "/opt/openworker/bundle",
    runner: "/opt/openworker/openworker",
    database_ca: "/private/pg.ca.crt",
    api_token_file: "/not-mounted",
    artifacts: {
      ...values.config.artifacts,
      ca_file: "/private/objects.ca.crt",
    },
    openshell: {
      ...values.config.openshell,
      certificates: "/no-gateway-credentials",
    },
  };
  delete config.telemetry;
  await privateWrite(
    join(dir, "private/reference.json"),
    JSON.stringify(config),
  );
  const proxy = (upstream, cert) =>
    `pid /tmp/nginx.pid; error_log /dev/stderr warn; events {} http { access_log off; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy; fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; server { listen 8443 ssl; ssl_certificate /private/${cert}.crt; ssl_certificate_key /private/${cert}.key; ssl_protocols TLSv1.2 TLSv1.3; client_max_body_size 65m; location / { proxy_pass http://${upstream}; proxy_set_header Host $http_host; proxy_buffering off; proxy_request_buffering off; } } }`;
  await privateWrite(
    join(dir, "private/objects.conf"),
    proxy("objects:8333", "objects"),
  );
  await privateWrite(
    join(dir, "private/api.conf"),
    proxy("127.0.0.1:8080", "api"),
  );
  await privateWrite(
    join(dir, "private/edge.conf"),
    `pid /tmp/nginx.pid; error_log /dev/stderr warn; events {} http { access_log off; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy; fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; client_max_body_size 65m; server { listen 8443 ssl; ssl_certificate /private/api.crt; ssl_certificate_key /private/api.key; location / { proxy_pass https://api:8443; proxy_ssl_verify on; proxy_ssl_trusted_certificate /private/api.ca.crt; proxy_ssl_server_name on; proxy_ssl_name localhost; proxy_buffering off; proxy_request_buffering off; } } server { listen 8444 ssl; ssl_certificate /private/objects.crt; ssl_certificate_key /private/objects.key; location / { proxy_pass http://objects:8333; proxy_set_header Host $http_host; proxy_buffering off; proxy_request_buffering off; } } }`,
  );
  const file = (f, dest) => `${join(dir, "private", f)}:${dest}:ro`;
  const common = {
    networks: ["isolated"],
    mem_limit: "384m",
    labels: { "openworker.dev/recovery": id },
    logging: {
      driver: "local",
      options: { "max-size": "5m", "max-file": "2" },
    },
  };
  const hard = {
    read_only: true,
    cap_drop: ["ALL"],
    security_opt: ["no-new-privileges:true"],
    tmpfs: ["/tmp:size=16m"],
  };
  const spec = {
    services: {
      postgres: {
        ...common,
        image: pgImage,
        environment: {
          POSTGRES_USER: "openworker",
          POSTGRES_DB: "openworker",
          POSTGRES_PASSWORD_FILE: "/private/pg-password",
        },
        command: [
          "sh",
          "-ec",
          "cp /private/pg.crt /tmp/tls.crt; cp /private/pg.key /tmp/tls.key; chown postgres:postgres /tmp/tls.*; chmod 600 /tmp/tls.key; exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/tls.crt -c ssl_key_file=/tmp/tls.key -c hba_file=/private/pg_hba.conf",
        ],
        volumes: [
          "database:/var/lib/postgresql/data",
          ...["pg.crt", "pg.key", "pg-password", "pg_hba.conf"].map((f) =>
            file(f, "/private/" + f),
          ),
        ],
        healthcheck: {
          test: ["CMD", "pg_isready", "-U", "openworker"],
          interval: "1s",
          timeout: "2s",
          retries: 40,
        },
      },
      objects: {
        ...common,
        image: s3Image,
        command: [
          "mini",
          "-dir=/data",
          "-s3.config=/private/s3.json",
          "-bucket=openworker-artifacts",
          "-admin.ui=false",
          "-webdav=false",
          "-volume.max=1",
        ],
        volumes: ["objects:/data", file("s3.json", "/private/s3.json")],
      },
      "objects-tls": {
        ...common,
        ...hard,
        user: "1000:1000",
        image: nginx,
        command: ["nginx", "-c", "/private/objects.conf", "-g", "daemon off;"],
        depends_on: ["objects"],
        networks: { isolated: { aliases: [endpoint.hostname] } },
        volumes: ["objects.conf", "objects.crt", "objects.key"].map((f) =>
          file(f, "/private/" + f),
        ),
      },
      api: {
        ...common,
        ...hard,
        image: "openworker-harness:taskdesk-20261011",
        user: "1000:1000",
        env_file: [join(dir, "private/api.env")],
        command: [
          "--config",
          "/private/reference.json",
          "api",
          "--node",
          "recovery-api",
          "--listen",
          "127.0.0.1:8080",
        ],
        tmpfs: ["/tmp:size=16m", "/state:uid=1000,gid=1000,mode=0700,size=32m"],
        volumes: [
          `${join(root, "bin/server")}:/opt/openworker/openworker-server-reference:ro`,
          ...["reference.json", "pg.ca.crt", "objects.ca.crt"].map((f) =>
            file(f, "/private/" + f),
          ),
        ],
      },
      "api-tls": {
        ...hard,
        user: "1000:1000",
        image: nginx,
        network_mode: "service:api",
        depends_on: ["api"],
        command: ["nginx", "-c", "/private/api.conf", "-g", "daemon off;"],
        volumes: ["api.conf", "api.crt", "api.key"].map((f) =>
          file(f, "/private/" + f),
        ),
        labels: { "openworker.dev/recovery": id },
      },
      edge: {
        ...hard,
        image: nginx,
        user: "1000:1000",
        networks: ["isolated", "ingress"],
        depends_on: ["api-tls", "objects"],
        ports: ["127.0.0.1::8443", "127.0.0.1::8444"],
        command: ["nginx", "-c", "/private/edge.conf", "-g", "daemon off;"],
        volumes: [
          "edge.conf",
          "api.crt",
          "api.key",
          "api.ca.crt",
          "objects.crt",
          "objects.key",
        ].map((f) => file(f, "/private/" + f)),
        labels: { "openworker.dev/recovery": id },
      },
    },
    networks: {
      isolated: { internal: true, labels: { "openworker.dev/recovery": id } },
      ingress: { labels: { "openworker.dev/recovery": id } },
    },
    volumes: {
      database: { labels: { "openworker.dev/recovery": id } },
      objects: { labels: { "openworker.dev/recovery": id } },
    },
  };
  // Host parent directory is 0700. Individual copied files mounted into the
  // non-root proxy must be readable there; private keys mount only in proxies.
  for (const f of [
    "reference.json",
    "pg.ca.crt",
    "objects.ca.crt",
    "objects.key",
    "api.key",
    "objects.conf",
    "objects.crt",
    "api.conf",
    "api.crt",
    "api.ca.crt",
    "edge.conf",
  ])
    await chmod(join(dir, "private", f), 0o644);
  await privateWrite(join(dir, "compose.json"), JSON.stringify(spec, null, 2));
  projects.push(s);
  await compose(s, [
    "up",
    "-d",
    "--wait",
    "--wait-timeout",
    "60",
    "postgres",
    "objects",
    "objects-tls",
  ]);
  return s;
}
async function restoreDB(s, dump) {
  const n = await sql(
    s,
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','S','v','m','f')",
  );
  assert.equal(n, "0", "Refuse nonempty restore destination");
  await compose(
    s,
    [
      "exec",
      "-T",
      "postgres",
      "pg_restore",
      "-U",
      "openworker",
      "-d",
      "openworker",
      "--no-owner",
      "--no-privileges",
      "--single-transaction",
      "--exit-on-error",
    ],
    dump,
  );
}
const catalog = async (s) =>
  JSON.parse(
    await sql(
      s,
      "SELECT coalesce(json_agg(data ORDER BY id),'[]'::json) FROM ow_helm.artifacts",
    ),
  );
function curlConfig(options) {
  return (
    Object.entries(options)
      .map(([k, v]) => (v === true ? k : `${k} = ${JSON.stringify(String(v))}`))
      .join("\n") + "\n"
  );
}
async function object(port, access, key, name, body) {
  assert.ok(/^[A-Za-z0-9_/-]+$/.test(name));
  const config = {
    url: `${endpoint.origin}/openworker-artifacts/${name}`,
    "connect-to": `${endpoint.hostname}:8443:127.0.0.1:${port}`,
    cacert: join(sourceRoot, "private/objects.ca.crt"),
    "aws-sigv4": "aws:amz:us-east-1:s3",
    user: access + ":" + key,
    noproxy: "*",
    "max-time": "20",
    silent: true,
    "show-error": true,
    "fail-with-body": true,
  };
  const args = ["--config", "-"];
  if (body) {
    // bytes live in a private file; key/credentials are only in config stdin.
    config.request = "PUT";
    args.push("--data-binary", "@" + body);
  }
  return command("curl", args, {
    input: curlConfig(config),
    limit: 2 * 1024 * 1024,
  });
}
async function startAPI(s) {
  await compose(s, ["up", "-d", "api", "api-tls", "edge"]);
  s.apiport = await port(s, "edge", 8443);
  s.s3port = await port(s, "edge", 8444);
  await wait(async () => (await request(s, "/readyz")).status === 200);
}
function request(s, path, { method = "GET", token = admin, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: "localhost",
        family: 4,
        port: s.apiport,
        path,
        method,
        ca,
        headers: {
          Authorization: "Bearer " + token,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
      },
      (res) => {
        const chunks = [];
        res.on("data", (b) => chunks.push(b));
        res.on("end", () =>
          resolve({ status: res.statusCode, bytes: Buffer.concat(chunks) }),
        );
        res.on("error", reject);
      },
    );
    req.setTimeout(15000, () => req.destroy(Error("request timeout")));
    req.on("error", reject);
    req.end(body ? JSON.stringify(body) : undefined);
  });
}
async function apiJSON(s, path) {
  const r = await request(s, path);
  assert.equal(r.status, 200, path);
  return JSON.parse(r.bytes);
}
async function replay(s, sid, cursor) {
  if (cursor === 0) return [];
  return new Promise((resolve, reject) => {
    let buffer = "",
      events = [],
      settled = false;
    const req = https.get(
      {
        hostname: "localhost",
        family: 4,
        port: s.apiport,
        path: `/sessions/${sid}/events?after=0`,
        ca,
        headers: { Authorization: "Bearer " + admin },
      },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          reject(Error("Replay rejected"));
          return;
        }
        res.on("error", (e) => {
          if (!settled) reject(e);
        });
        res.on("data", (b) => {
          buffer += b.toString();
          let at;
          while ((at = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, at);
            buffer = buffer.slice(at + 2);
            const line = frame.split("\n").find((x) => x.startsWith("data: "));
            if (!line) continue;
            try {
              const event = JSON.parse(line.slice(6));
              events.push(event);
              assert.equal(event.event_id, events.length);
              if (event.event_id === cursor) {
                settled = true;
                req.destroy();
                resolve(events);
                return;
              }
            } catch (e) {
              settled = true;
              req.destroy();
              reject(e);
              return;
            }
          }
        });
      },
    );
    req.setTimeout(15000, () => req.destroy(Error("Replay timeout")));
    req.on("error", (e) => {
      if (!settled) reject(e);
    });
  });
}
async function fixture(s, action) {
  const container = (await compose(s, ["ps", "-q", "api"])).toString().trim();
  assert.ok(container);
  return command("docker", [
    "run",
    "--rm",
    "--network",
    `${s.project}_isolated`,
    "--label",
    `openworker.dev/recovery=${id}`,
    "--env-file",
    join(s.dir, "private/api.env"),
    "-e",
    "OW_RECOVERY_FIXTURE=isolated-copy",
    "--mount",
    `type=bind,src=${join(root, "bin/fixture")},dst=/fixture,readonly`,
    "--mount",
    `type=bind,src=${join(s.dir, "private/pg.ca.crt")},dst=/private/pg.ca.crt,readonly`,
    "--mount",
    `type=bind,src=${join(root, "admin")},dst=/fixture-secrets/admin,readonly`,
    "--mount",
    `type=bind,src=${join(root, "revoked")},dst=/fixture-secrets/revoked,readonly`,
    "--entrypoint",
    "/fixture",
    "openworker-harness:taskdesk-20261011",
    action,
  ]);
}
try {
  const stage = await fresh("stage");
  await restoreDB(stage, originalDump);
  await startAPI(stage);
  pass(
    "actual Task Desk logical dump restored into an empty isolated database",
  );
  const originalCatalog = await catalog(stage);
  forward = spawn(
    "kubectl",
    [
      ...kargs,
      "port-forward",
      "service/objects",
      "0:8443",
      "--address",
      "127.0.0.1",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  forward.stderr.resume();
  const sourcePort = await new Promise((resolve, reject) => {
    let out = "";
    const timer = setTimeout(() => reject(Error("S3 forward timeout")), 15000);
    forward.stdout.on("data", (b) => {
      out += b;
      const match = out.match(/127\.0\.0\.1:(\d+) ->/);
      if (match) {
        clearTimeout(timer);
        resolve(Number(match[1]));
      }
    });
    forward.on("error", reject);
    forward.on("exit", () => {
      clearTimeout(timer);
      reject(Error("S3 forward ended"));
    });
  });
  const sourceKey = (
    await readFile(join(sourceRoot, "private/s3"), "utf8")
  ).trim();
  const first = join(root, "original-backup");
  await capture(first, originalDump, originalCatalog, (key) =>
    object(sourcePort, "local-harness", sourceKey, key),
  );
  await verify(first);
  forward.kill("SIGTERM");
  forward = undefined;
  pass(
    "source artifacts copied with full hash verification; source DB and objects unmodified",
  );
  const importObjects = async (s, dir, manifest) => {
    for (const e of manifest.objects)
      await object(s.s3port, "recovery", s.s3, e.key, join(dir, e.file));
  };
  await importObjects(stage, first, await verify(first));
  await fixture(stage, "seed");
  const baselineIds = JSON.parse(
      await sql(stage, "SELECT json_agg(id ORDER BY id) FROM ow_helm.sessions"),
    ),
    snapshots = {};
  for (const sid of baselineIds)
    snapshots[sid] = await apiJSON(stage, "/sessions/" + sid);
  await privateWrite(
    join(root, "expected-snapshots.json"),
    JSON.stringify(snapshots),
  );
  const stagedDump = await compose(stage, [
    "exec",
    "-T",
    "postgres",
    "pg_dump",
    "-U",
    "openworker",
    "-d",
    "openworker",
    "--format=custom",
    "--no-owner",
    "--no-privileges",
    "--schema=ow_helm",
  ]);
  const archive = join(root, "qualified-backup");
  await capture(archive, stagedDump, await catalog(stage), (key) =>
    object(stage.s3port, "recovery", stage.s3, key),
  );
  const manifest = await verify(archive);
  pass(
    "pending approval/question, uncertain tool outcome and revoked-key fixtures captured via domain store",
  );
  const target = await fresh("target");
  await restoreDB(target, await readFile(join(archive, "database.dump")));
  await startAPI(target);
  await importObjects(target, archive, manifest);
  assert.deepEqual(
    await catalog(target),
    manifest.catalog,
    "Restored catalog differs from backup",
  );
  for (const sid of baselineIds)
    assert.deepEqual(await apiJSON(target, "/sessions/" + sid), snapshots[sid]);
  pass(
    `${baselineIds.length} restored session snapshots match exactly, including pending identities/digests/history/cursors`,
  );
  for (const sid of baselineIds)
    assert.deepEqual(
      await replay(target, sid, snapshots[sid].cursor),
      await replay(stage, sid, snapshots[sid].cursor),
    );
  pass(
    "full contiguous event replay matches the backed-up snapshots for every session",
  );
  for (const a of manifest.catalog.filter((a) => a.status === "ready")) {
    const r = await request(
      target,
      `/sessions/${a.registration.session_id}/artifacts/${a.id}/content`,
    );
    assert.equal(r.status, 200);
    assert.equal(r.bytes.length, a.registration.expected.size);
    assert.equal(hash(r.bytes), a.registration.expected.sha256);
  }
  pass(
    `${manifest.catalog.filter((a) => a.status === "ready").length} published artifacts download from fresh S3 storage with original bindings and hashes`,
  );
  const part = manifest.objects.find(
    (e) => e.key.endsWith("/part-00") && e.size > 0,
  );
  const corrupt = join(root, "corrupt-part.bin");
  await privateWrite(corrupt, Buffer.alloc(part.size, 0x78));
  await object(target.s3port, "recovery", target.s3, part.key, corrupt);
  const a = manifest.catalog.find((a) => part.key.startsWith(a.key + "/"));
  let rejected = false;
  try {
    const bad = await request(
      target,
      `/sessions/${a.registration.session_id}/artifacts/${a.id}/content`,
    );
    rejected = bad.status !== 200;
  } catch {
    rejected = true;
  }
  assert.ok(rejected, "Corrupt restored artifact was served as successful");
  await object(
    target.s3port,
    "recovery",
    target.s3,
    part.key,
    join(archive, part.file),
  );
  const repaired = await request(
    target,
    `/sessions/${a.registration.session_id}/artifacts/${a.id}/content`,
  );
  assert.equal(repaired.status, 200);
  assert.equal(hash(repaired.bytes), a.registration.expected.sha256);
  pass(
    "corrupt object on restored storage fails download; verified backup repairs the exact object",
  );
  assert.equal(
    (await request(target, "/sessions", { token: revoked })).status,
    401,
  );
  pass("revoked credential remains rejected after restore");
  for (const [method, path] of [
    ["POST", "/sessions"],
    ["POST", "/sessions/s_backup_approval/controls"],
    ["POST", "/sessions/s_backup_question/turns"],
    ["POST", "/credentials"],
    ["PUT", "/sessions/s_backup_approval/artifacts/a_no/content"],
  ]) {
    const r = await request(target, path, { method, body: {} });
    assert.equal(r.status, 503);
    assert.equal(JSON.parse(r.bytes).error, "recovery_read_only");
  }
  pass(
    "recovery API rejects create, turn, approval, credential and artifact mutations",
  );
  assert.equal((await apiJSON(target, "/readyz")).recovery_read_only, true);
  let workerError;
  try {
    await compose(target, [
      "exec",
      "-T",
      "api",
      "/opt/openworker/openworker-server-reference",
      "--config",
      "/private/reference.json",
      "worker",
      "--node",
      "must-not-run",
    ]);
  } catch (e) {
    workerError = e;
  }
  assert.ok(workerError?.diagnostic, "Worker unexpectedly started");
  assert.match(
    await readFile(workerError.diagnostic, "utf8"),
    /recovery_read_only/,
  );
  const apiContainer = (await compose(target, ["ps", "-q", "api"]))
    .toString()
    .trim();
  const info = JSON.parse(
    (await command("docker", ["inspect", apiContainer])).toString(),
  )[0];
  assert.ok(
    !info.Config.Env.some((e) => /OPENAI_API_KEY|AWS_SESSION_TOKEN/.test(e)),
  );
  assert.ok(
    !info.Mounts.some((x) =>
      /openshell|gateway|docker.sock/.test(x.Destination),
    ),
  );
  assert.equal(
    JSON.parse(
      (
        await command("docker", [
          "network",
          "inspect",
          `${target.project}_isolated`,
        ])
      ).toString(),
    )[0].Internal,
    true,
  );
  pass(
    "worker start refused; no model/gateway credentials or outbound network in restored deployment",
  );
  await assert.rejects(restoreDB(target, stagedDump), /nonempty/);
  pass("nonempty restore target refused without overwrite");
  await fixture(target, "recover");
  pass(
    "explicit domain recovery preserves pending questions/approvals and interrupts unknown effects without replay",
  );
  // Restart every restored API process, without touching the source app.
  await compose(target, ["stop", "api-tls"]);
  await compose(target, ["restart", "api"]);
  await compose(target, ["up", "-d", "api-tls"]);
  await wait(async () => (await request(target, "/readyz")).status === 200);
  const pending = await apiJSON(target, "/sessions/s_backup_approval");
  assert.equal(pending.interactions[0].response, null);
  pass("restored API restart keeps the pending approval unanswered");
  await privateWrite(
    join(root, "qualification.json"),
    JSON.stringify(
      {
        time: new Date().toISOString(),
        source: "existing local Task Desk read-only snapshot",
        checks,
        sessions: baselineIds.length,
        artifacts: manifest.catalog.length,
        archive_bytes: manifest.total_bytes,
        source_mutated: false,
        workspace_restored: false,
        automatic_replay: false,
      },
      null,
      2,
    ),
  );
  console.log(
    "Qualification passed. Removing only owned drill containers/volumes; private backups and evidence retained.",
  );
} finally {
  forward?.kill("SIGTERM");
  // Exact project names were created above; never use Docker pruning/global deletion.
  const cleanupErrors = [];
  for (const s of projects.reverse()) {
    try {
      await compose(s, ["down", "--volumes", "--remove-orphans"]);
    } catch {
      cleanupErrors.push(s.project);
    }
  }
  const remaining = {};
  for (const [kind, args] of [
    ["containers", ["ps", "-a", "-q"]],
    ["volumes", ["volume", "ls", "-q"]],
    ["networks", ["network", "ls", "-q"]],
  ]) {
    remaining[kind] = (
      await command("docker", [
        ...args,
        "--filter",
        `label=openworker.dev/recovery=${id}`,
      ])
    )
      .toString()
      .trim();
  }
  const removed =
    cleanupErrors.length === 0 && Object.values(remaining).every((x) => !x);
  await privateWrite(
    join(root, "cleanup.json"),
    JSON.stringify({
      time: new Date().toISOString(),
      projects: projects.map((s) => s.project),
      removed,
      remaining,
      cleanupErrors,
    }),
  );
  assert.ok(removed, "Owned recovery resources remain; inspect cleanup.json");
}
