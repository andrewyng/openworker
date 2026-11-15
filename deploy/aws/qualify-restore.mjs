// Logical RDS restore to a NEW database, read-only API, original S3 identity.
// Does not claim independent object-store DR, RDS failover or sandbox restoration.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { repo, run, kubectl, apply } from "../helm/local.mjs";
import {
  root,
  context,
  save,
  wait,
  pods,
  forward,
  client,
  planTool,
  permitPodIdentity,
  postgresEnvironment,
} from "./qualification-lib.mjs";
const m = await context(),
  k = (args) => kubectl(m, ["-n", "ow-app", ...args]),
  checks = [],
  forwards = [];
const v = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
const proof = JSON.parse(
  await readFile(join(root, "current-http-qualification.json"), "utf8"),
);
const env = JSON.parse(await readFile(join(root, "environment.json"), "utf8"));
const suffix = randomUUID().replaceAll("-", "").slice(0, 10),
  database = "ow_recovery_" + suffix,
  helper = "ow-restore-" + suffix;
const meta = (name) => ({
  name,
  namespace: "ow-app",
  labels: { "openworker.dev/experiment": m.context },
});
const pgImage =
  v.databaseReadiness.image ||
  "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685";
assert.match(database, /^ow_recovery_[a-f0-9]{10}$/);
assert.equal(v.config.schema, "ow_taskdesk");
const pg = (script) =>
  k(["exec", "deployment/" + helper, "--", "sh", "-ec", script]);
let completed = false;
try {
  const credentials = JSON.parse(
    await k(["get", "secret", v.secrets.database, "-o", "json"]),
  );
  const pgEnv = postgresEnvironment(
    Buffer.from(credentials.data.url, "base64").toString(),
  );
  await apply(m, [
    {
      apiVersion: "v1",
      kind: "Secret",
      metadata: meta(helper + "-pg"),
      stringData: pgEnv,
    },
  ]);
  const f = await forward(m, (await pods(m, "api"))[0].metadata.name);
  forwards.push(f);
  const source = await client(f.port);
  const sid = (
    await source.json("/sessions", {
      request_id: randomUUID(),
      parameters: {
        name: "Restore pending approval",
        coworker: "task-desk",
        workspace: "/sandbox",
        environment_id: env.id,
      },
    })
  ).session_id;
  await source.json(`/sessions/${sid}/turns`, {
    submission_id: randomUUID(),
    text: planTool("write_file", {
      path: "restore-not-approved-" + suffix,
      content: "must remain pending",
    }),
  });
  const expected = await source.state(sid, "waiting"),
    artifactSnapshot = await source.snapshot(proof.session);
  await apply(m, [
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: meta(helper),
      spec: {
        replicas: 1,
        selector: { matchLabels: { "ow-restore-helper": helper } },
        template: {
          metadata: {
            labels: {
              ...meta(helper).labels,
              "ow-restore-helper": helper,
              "app.kubernetes.io/instance": "ow",
              "app.kubernetes.io/name": "openworker",
            },
          },
          spec: {
            restartPolicy: "Always",
            automountServiceAccountToken: false,
            securityContext: {
              runAsUser: 1000,
              runAsGroup: 1000,
              fsGroup: 1000,
            },
            containers: [
              {
                name: "pg",
                image: pgImage,
                command: ["sleep", "1800"],
                envFrom: [{ secretRef: { name: helper + "-pg" } }],
                env: [
                  { name: "PGSSLMODE", value: "verify-full" },
                  { name: "PGSSLROOTCERT", value: "/database-ca/ca.crt" },
                ],
                securityContext: {
                  allowPrivilegeEscalation: false,
                  capabilities: { drop: ["ALL"] },
                  seccompProfile: { type: "RuntimeDefault" },
                },
                resources: {
                  requests: { cpu: "50m", memory: "64Mi" },
                  limits: { cpu: "500m", memory: "256Mi" },
                },
                volumeMounts: [
                  { name: "ca", mountPath: "/database-ca", readOnly: true },
                  { name: "tmp", mountPath: "/tmp" },
                ],
              },
            ],
            volumes: [
              {
                name: "ca",
                secret: { secretName: v.secrets.databaseCA, defaultMode: 288 },
              },
              { name: "tmp", emptyDir: {} },
            ],
          },
        },
      },
    },
  ]);
  await k(["rollout", "status", "deployment/" + helper, "--timeout=120s"]);
  // No credentials in command arguments. The mounted Secret supplies libpq env.
  await pg(
    "pg_dump --format=custom --no-owner --no-privileges --schema=ow_taskdesk --file=/tmp/database.dump; test $(wc -c </tmp/database.dump) -lt 67108864",
  );
  const dump = Buffer.from(await pg("base64 /tmp/database.dump"), "base64");
  await save("rds-restore-dump-metadata.json", {
    size: dump.length,
    sha256: createHash("sha256").update(dump).digest("hex"),
    database,
    sourceSchema: v.config.schema,
  });
  // Keep private source dump for evidence/recovery; it contains session content.
  const { writeFile } = await import("node:fs/promises");
  await writeFile(join(root, database + ".dump"), dump, {
    mode: 0o600,
    flag: "wx",
  });
  await pg(
    `printf 'CREATE DATABASE ${database};' | psql -X -v ON_ERROR_STOP=1`,
  );
  await pg(
    `pg_restore --dbname=${database} --single-transaction --exit-on-error --no-owner --no-privileges /tmp/database.dump`,
  );
  const secret = JSON.parse(
    await k(["get", "secret", v.secrets.database, "-o", "json"]),
  );
  const url = new URL(Buffer.from(secret.data.url, "base64").toString());
  url.pathname = "/" + database;
  await apply(m, [
    {
      apiVersion: "v1",
      kind: "Secret",
      metadata: meta(helper),
      stringData: { url: url.toString() },
    },
  ]);
  const rv = structuredClone(v);
  rv.api.replicas = 1;
  rv.worker.replicas = 0;
  rv.taskDesk.enabled = false;
  rv.config.recovery_read_only = true;
  rv.secrets.database = helper;
  rv.secrets.model = "";
  await permitPodIdentity(m, "restore");
  await save("restore-values.json", rv);
  await run(m.helm, [
    "upgrade",
    "--install",
    "restore",
    join(repo, "deploy/helm/openworker"),
    "--kubeconfig",
    m.kubeconfig,
    "--kube-context",
    m.context,
    "-n",
    "ow-app",
    "-f",
    join(root, "restore-values.json"),
  ]);
  await k([
    "rollout",
    "status",
    "deployment/restore-openworker-api",
    "--timeout=150s",
  ]);
  const rf = await forward(
    m,
    (await pods(m, "api", "restore"))[0].metadata.name,
  );
  forwards.push(rf);
  const restored = await client(rf.port);
  const actual = await restored.snapshot(sid);
  assert.deepEqual(actual, expected);
  assert.deepEqual(await restored.snapshot(proof.session), artifactSnapshot);
  assert.deepEqual(
    await restored.events(sid, actual.cursor),
    await source.events(sid, expected.cursor),
  );
  checks.push(
    "Fresh RDS database restores exact snapshots, pending approval binding, IDs and contiguous events",
  );
  const r = await restored.request(
    `/sessions/${proof.session}/artifacts/${proof.artifact}/content`,
  );
  assert.equal(r.status, 200);
  assert.equal(
    createHash("sha256").update(r.bytes).digest("hex"),
    proof.artifact_sha256,
  );
  checks.push(
    "Restored artifact metadata retrieves checksum-identical bytes from the retained original S3 store",
  );
  const mutation = await restored.request(`/sessions/${sid}/controls`, {
    body: {
      request_id: randomUUID(),
      turn_id: expected.turn.id,
      action: { kind: "cancel" },
    },
  });
  assert.equal(mutation.status, 503);
  assert.match(mutation.bytes.toString(), /recovery_read_only/);
  assert.equal((await pods(m, "worker", "restore")).length, 0);
  const dep = JSON.parse(
    await k(["get", "deployment/restore-openworker-api", "-o", "json"]),
  );
  assert.ok(
    !dep.spec.template.spec.volumes.some((v) => v.name === "openshell"),
  );
  assert.ok(!dep.spec.template.spec.containers[0].envFrom);
  assert.deepEqual(await source.snapshot(sid), expected);
  checks.push(
    "Inspection-only recovery has zero workers, no gateway/model credentials and refuses mutations; original state unchanged",
  );
  await save("current-restore-qualification.json", {
    status: "completed",
    time: new Date().toISOString(),
    database,
    session: sid,
    checks,
    limits: [
      "Logical PostgreSQL dump/restore, not RDS snapshot/PITR/failover",
      "Original S3 retained; no independent object-store restore claim",
      "Sandbox volume not restored; execution deliberately disabled",
    ],
  });
  for (const c of checks) console.log("PASS " + c);
  completed = true;
} finally {
  for (const f of forwards) f.close();
  await save("current-restore-progress.json", { database, helper, checks });
  if (completed)
    await k([
      "delete",
      "deployment",
      helper,
      "--ignore-not-found=true",
      "--wait=false",
    ]).catch(() => {});
  if (completed)
    await k([
      "delete",
      "secret",
      helper + "-pg",
      "--ignore-not-found=true",
    ]).catch(() => {});
  // Isolated DB and read-only deployment remain for inspection until owned-stack teardown.
}
