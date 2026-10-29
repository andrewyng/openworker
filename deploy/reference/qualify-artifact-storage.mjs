// Disposable local storage qualification. Never uses AWS credentials or a gateway.
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { get } from "node:http";
const exec = promisify(execFile);
const repo = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const id = randomUUID().replaceAll("-", "").slice(0, 12);
const root = resolve(process.argv[2] ?? join(repo, `.ow-service-artifacts-${id}`));
await mkdir(root, { mode: 0o700 }); // Never reuse another run's state or credentials.
const images = {
  pg: "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
  s3: "chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d",
};
const names = { pg: `ow-artifact-pg-${id}`, s3: `ow-artifact-s3-${id}` };
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("AWS_") && key !== "OW_TEST_ARTIFACT_CONFIG"));
const runner = resolve(env.OW_PROBE_RUNNER ?? join(repo, ".ow-service-build/out/openworker"));
assert.equal((await readFile(runner)).subarray(0, 4).toString("hex"), "7f454c46", "Set OW_PROBE_RUNNER to an existing Linux ELF runner; it is validated but never executed");
env.OW_PROBE_RUNNER = runner;
const docker = async (...args) => (await exec("docker", args, { env, timeout: 60000, maxBuffer: 1048576 })).stdout.trim();
const secret = () => randomBytes(24).toString("hex");
const password = secret(), access = `owlocal${id}`, key = secret();
await writeFile(join(root, "postgres-password"), password, { mode: 0o600, flag: "wx" });
await writeFile(join(root, "s3.json"), JSON.stringify({ identities: [{ name: "storage-test", credentials: [{ accessKey: access, secretKey: key }], actions: ["Read:openworker-artifacts", "Write:openworker-artifacts", "List:openworker-artifacts"] }] }), { mode: 0o600, flag: "wx" });
const owned = [];
async function runContainer(kind, args) {
  await docker("image", "inspect", images[kind]); // Reuse pinned local caches; no implicit pull.
  owned.push(kind); // Includes an uncertain create; cleanup inspects its exact label.
  await writeFile(join(root, `${kind}-create-intent.json`), JSON.stringify({ name: names[kind], image: images[kind], label: id }), { mode: 0o600, flag: "wx" });
  const container = await docker("run", "--pull=never", "-d", "--name", names[kind], "--label", `openworker.artifact-qualification=${id}`, ...args, images[kind], ...(kind === "s3" ? ["mini", "-dir=/data", "-s3.config=/run/secrets/s3.json", "-bucket=openworker-artifacts", "-admin.ui=false", "-webdav=false", "-volume.max=1"] : []));
  await writeFile(join(root, `${kind}-container.json`), JSON.stringify({ id: container, name: names[kind] }), { mode: 0o600, flag: "wx" });
  const inspect = JSON.parse(await docker("inspect", names[kind]))[0];
  assert.equal(inspect.Config.Labels["openworker.artifact-qualification"], id);
  const binding = inspect.NetworkSettings.Ports[kind === "pg" ? "5432/tcp" : "8333/tcp"];
  assert.equal(binding.length, 1); assert.equal(binding[0].HostIp, "127.0.0.1");
  return Number(binding[0].HostPort);
}
async function until(check) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw Error("Fixture readiness not confirmed; inspect private logs");
}
function bucketStatus(url) {
  return new Promise((resolve, reject) => {
    const request = get(url, response => { response.resume(); resolve(response.statusCode); });
    request.setTimeout(2000, () => request.destroy(Error("Fixture probe timed out")));
    request.on("error", reject);
  });
}
async function test(label, args, additions = {}) {
  const log = join(root, label + ".log");
  const child = spawn("cargo", ["test", "--locked", "--manifest-path", join(repo, "harness/Cargo.toml"), ...args], { cwd: repo, env: { ...env, ...additions }, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", b => { output += b; }); child.stderr.on("data", b => { output += b; });
  const timer = setTimeout(() => child.kill("SIGTERM"), 300000);
  const status = await new Promise((resolve, reject) => { child.on("error", reject); child.on("close", resolve); });
  clearTimeout(timer);
  await writeFile(log, output, { mode: 0o600, flag: "wx" });
  assert.equal(status, 0, `${label} failed; inspect private ${log}`);
  console.log(`PASS ${label}`);
}
let passed = false;
const removed = [];
try {
  const pg = await runContainer("pg", ["--memory=512m", "--tmpfs", "/var/lib/postgresql/data:rw,size=512m", "-p", "127.0.0.1::5432", "-e", "POSTGRES_USER=openworker", "-e", "POSTGRES_DB=openworker", "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/password", "--mount", `type=bind,source=${join(root, "postgres-password")},target=/run/secrets/password,readonly`]);
  const s3 = await runContainer("s3", ["--memory=768m", "--tmpfs", "/data:rw,size=512m", "-p", "127.0.0.1::8333", "--mount", `type=bind,source=${join(root, "s3.json")},target=/run/secrets/s3.json,readonly`]);
  await until(async () => { await docker("exec", names.pg, "pg_isready", "-U", "openworker", "-d", "openworker"); return true; });
  await until(async () => (await bucketStatus(`http://127.0.0.1:${s3}/openworker-artifacts`)) === 403);
  console.log("Disposable PostgreSQL and S3 fixtures ready");
  const database = { OW_PG_TEST_URL: `postgresql://openworker:${password}@127.0.0.1:${pg}/openworker` };
  await test("filesystem-api-restart", ["-p", "openworker-server-reference", "--test", "artifact_storage", "--", "--ignored", "--nocapture"], database);
  const storage = { AWS_ACCESS_KEY_ID: access, AWS_SECRET_ACCESS_KEY: key, OW_TEST_S3_ENDPOINT: `http://127.0.0.1:${s3}` };
  await test("s3-immutable-streaming", ["-p", "openworker-artifacts", "--all-features", "--test", "store", "s3_atomic_streaming_resume_and_conflicts", "--", "--ignored", "--nocapture"], storage);
  await test("s3-api-restart", ["-p", "openworker-server-reference", "--test", "artifact_storage", "--", "--ignored", "--nocapture"], { ...database, ...storage, OW_TEST_ARTIFACT_CONFIG: JSON.stringify({ kind: "s3", deployment: `local-${id}`, bucket: "openworker-artifacts", region: "us-east-1", endpoint: storage.OW_TEST_S3_ENDPOINT }) });
  passed = true;
} finally {
  for (const kind of owned.reverse()) {
    const inspect = JSON.parse(await docker("inspect", names[kind]))[0];
    assert.equal(inspect.Config.Labels["openworker.artifact-qualification"], id);
    await writeFile(join(root, `${kind}-logs.txt`), await docker("logs", names[kind]).catch(() => "logs unavailable"), { mode: 0o600 });
    await docker("rm", "-f", "-v", inspect.Id);
    const absent = await docker("ps", "-a", "--filter", `id=${inspect.Id}`, "--format", "{{.ID}}");
    assert.equal(absent, ""); removed.push(kind);
  }
  await writeFile(join(root, "qualification.json"), JSON.stringify({ passed, fixturesRemoved: removed, images, cloudCalls: false, liveModelCalls: false, actualPodIdentityQualified: false, privateLogs: root }, null, 2));
}
console.log(JSON.stringify({ passed, fixturesRemoved: removed, root }));
