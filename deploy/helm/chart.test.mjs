import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, repo } from "./local.mjs";
const helm = process.env.HELM || "helm",
  digest = "sha256:" + "a".repeat(64);
const values = {
  image: "example/harness@" + digest,
  config: {
    database_env: "OW_SERVER_DATABASE_URL",
    schema: "ow_test",
    database_ca: "/database-ca/ca.crt",
    state_root: "/state",
    bundle: "/opt/openworker/bundle",
    runner: "/opt/openworker/openworker",
    model: "fixture",
    scripted: true,
    api_token_file: "/bootstrap/admin",
    actor: "test",
    openshell: {
      identity: "test",
      endpoint: "https://gateway.example:8080",
      certificates: "/openshell",
      workspace: "default",
      image: "example/runner@" + digest,
    },
  },
  secrets: {
    database: "db",
    databaseCA: "db-ca",
    openshell: "shell",
    apiTLS: "tls",
    probe: "probe",
  },
};
async function render(v) {
  const root = await mkdtemp(join(tmpdir(), "ow-chart-"));
  try {
    const p = join(root, "values.json");
    await writeFile(p, JSON.stringify(v));
    return await run(helm, [
      "template",
      "test",
      join(repo, "deploy/helm/openworker"),
      "-f",
      p,
    ]);
  } finally {
    await rm(root, { recursive: true });
  }
}
test("operator Git grants render only non-secret pins and reject credential/selection fields", async()=>{
  const v=structuredClone(values);
  const grant={provider:{name:"github",provider_id:"original",profile_id:"github",profile_workspace:"",profile_digest:"a".repeat(64)},scope:{host:"github.com",repositories:["org/repo"]}};
  v.config.openshell.git_grants={"github@1":grant};
  const out=await render(v);
  assert.match(out,/git_grants/);assert.match(out,/github@1/);
  for(const mutate of [
    g=>g.config.openshell.git_grants["github@1"].apiKey="forbidden",
    g=>g.config.openshell.selected_git_grant="github@1",
    g=>g.config.openshell.git_grants["github@1"].provider.token="forbidden",
  ]){const bad=structuredClone(v);mutate(bad);await assert.rejects(render(bad));}
});
test("separate hardened roles and TLS-only service; chart embeds no credentials or databases", async () => {
  const out = await render(values);
  assert.equal((out.match(/kind: Deployment/g) || []).length, 2);
  assert.equal(
    (out.match(/automountServiceAccountToken: false/g) || []).length,
    2,
  );
  assert.equal((out.match(/readOnlyRootFilesystem: true/g) || []).length, 3);
  assert.match(out, /127\.0\.0\.1:8080/);
  assert.match(out, /targetPort: https/);
  assert.match(out, /subPath: reference.json/);
  assert.match(out, /proxy_buffering off/);
  assert.match(out, /items: \[\{key: ca.crt, path: ca.crt\}\]/);
  assert.match(out, /kind: NetworkPolicy/);
  assert.doesNotMatch(
    out,
    /kind: Secret|kind: StatefulSet|hostPath:|privileged: true|NodePort|LoadBalancer/,
  );
  assert.doesNotMatch(out, /mountPath: \/bootstrap/);
});
test("chart fails closed on missing secrets, mutable images, plaintext DB and invalid capacity", async () => {
  for (const change of [
    (v) => (v.secrets.database = ""),
    (v) => (v.image = "example:latest"),
    (v) => (v.config.database_loopback_plaintext = true),
    (v) => (v.config.openshell.endpoint = "http://gateway:8080"),
    (v) => (v.worker = { replicas: 2, slots: 0 }),
    (v) => (v.config.artifacts = { kind: "filesystem" }),
    (v) => (v.unknown = "typo"),
  ]) {
    const v = structuredClone(values);
    change(v);
    await assert.rejects(render(v));
  }
});
test("planned drain has an explicit rolling policy and sufficient termination budget", async () => {
  const out = await render(values);
  assert.match(out, /maxUnavailable: 0, maxSurge: 1/);
  assert.match(out, /terminationGracePeriodSeconds: 360/);
  assert.match(out, /--drain-timeout-seconds.*300/);
  assert.match(out, /--health-listen.*127\.0\.0\.1:8081/);
  assert.match(out, /http:\/\/127\.0\.0\.1:8081\/readyz/);
  const v = structuredClone(values);
  v.terminationGracePeriodSeconds = 60;
  await assert.rejects(render(v));
  v.worker = {
    replicas: 2,
    slots: 1,
    drainTimeoutSeconds: 10,
    shutdownTimeoutSeconds: 30,
  };
  assert.match(await render(v), /terminationGracePeriodSeconds: 60/);
  v.worker.drainTimeoutSeconds = 0;
  await assert.rejects(render(v));
  v.worker = { replicas: 0, slots: 1 };
  v.config.recovery_read_only = true;
  assert.match(await render(v), /terminationGracePeriodSeconds: 60/);
});
test("operational sampling and deployment identity are optional and bounded", async () => {
  const v = structuredClone(values);
  v.config.telemetry = {
    endpoint: "https://collector.example:4318",
    deployment: "evaluation",
  };
  v.config.operational_metrics = {
    sample_interval_seconds: 10,
    stalled_after_seconds: 300,
    recent_window_seconds: 300,
  };
  const out = await render(v);
  assert.match(out, /operational_metrics/);
  assert.match(out, /evaluation/);
  v.config.operational_metrics.sample_interval_seconds = 0;
  await assert.rejects(render(v));
  v.config.operational_metrics.sample_interval_seconds = 10;
  v.config.telemetry.deployment = "secret@example.com";
  await assert.rejects(render(v));
});
test("API S3 credentials and worker model credentials are not shared", async () => {
  const v = structuredClone(values);
  v.secrets.objectStore = "artifact-credential";
  v.secrets.model = "model-credential";
  const out = await render(v),
    api = out.slice(
      out.indexOf("name: test-openworker-api"),
      out.indexOf("name: test-openworker-worker"),
    ),
    worker = out.slice(out.indexOf("name: test-openworker-worker"));
  assert.match(api, /artifact-credential/);
  assert.doesNotMatch(api, /model-credential/);
  assert.match(worker, /model-credential/);
  assert.doesNotMatch(worker, /artifact-credential/);
});
test("provider connections carry identities and secret references, never inline keys", async () => {
  const v = structuredClone(values);
  v.config.model_provider = "openai";
  v.config.model_connections = [
    {id: "openai", provider: "openai", identity: "deployment-openai-v1", key_env: "OPENAI_API_KEY"},
    {id: "anthropic", provider: "anthropic", identity: "deployment-anthropic-v1", key_env: "ANTHROPIC_API_KEY"},
  ];
  assert.match(await render(v), /deployment-anthropic-v1/);
  v.config.model_connections[0].api_key = "synthetic-not-a-real-key";
  await assert.rejects(render(v));
  delete v.config.model_connections[0].api_key;
  v.config.model_connections[1].provider = "unsupported";
  await assert.rejects(render(v));
});
test("Task Desk is optional, private, single-replica and has no service token or admin key", async () => {
  const v = structuredClone(values);
  v.api = { replicas: 2, serviceAccountName: "artifact-api" };
  v.taskDesk = {
    enabled: true,
    image: "example/taskdesk@" + digest,
    accessSecret: "desk-access",
    apiCASecret: "api-ca",
    config: {
      port: 54350,
      origin: "http://127.0.0.1:54351",
      api_token_file: "/access/operator",
      access_key_file: "/access/access",
    },
  };
  const out = await render(v);
  assert.match(out, /serviceAccountName: "artifact-api"/);
  assert.equal((out.match(/kind: Deployment/g) || []).length, 3);
  assert.match(out, /http:\/\/127.0.0.1:54350/);
  assert.match(out, /http:\/\/127.0.0.1:54351/);
  assert.match(out, /headers:\{host\}/); // Probe uses configured Host, connects only to loopback.
  assert.doesNotMatch(out, /kind: Ingress|NodePort|LoadBalancer/);
  const app = out.slice(
    out.indexOf(
      "name: test-openworker-taskdesk",
      out.indexOf("kind: Deployment"),
    ),
  );
  assert.match(app, /secretName: desk-access/);
  assert.doesNotMatch(app, /secretName: bootstrap/);
  v.taskDesk.config.private_network = true;
  await assert.rejects(render(v));
});
test("optional bounded database readiness has CA verification and no database/model password", async () => {
  const v = structuredClone(values);
  v.databaseReadiness = {
    enabled: true,
    host: "database.internal",
    image: "example/pg@" + digest,
  };
  const out = await render(v);
  assert.equal((out.match(/name: database-ready/g) || []).length, 2);
  const init = out.slice(
    out.indexOf("initContainers:"),
    out.indexOf("      containers:", out.indexOf("initContainers:")),
  );
  assert.match(init, /verify-full/);
  assert.doesNotMatch(init, /PGPASSWORD|secretKeyRef|envFrom/);
  v.databaseReadiness.host = "";
  await assert.rejects(render(v));
});
test("recovery rendering requires zero workers and no model or gateway credential mount", async () => {
  const v = structuredClone(values);
  v.config.recovery_read_only = true;
  await assert.rejects(render(v));
  v.worker = { replicas: 0, slots: 1 };
  v.secrets.openshell = "";
  const out = await render(v);
  assert.match(out, /"recovery_read_only": true/);
  assert.doesNotMatch(out, /mountPath: \/openshell|name: openshell/);
  v.secrets.model = "must-not-mount";
  await assert.rejects(render(v));
});
