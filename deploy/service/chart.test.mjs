import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { run } from "./stack.mjs";
const base = {
  image: "example@sha256:" + "a".repeat(64),
  config: {
    service: {
      id: "service-test",
      namespace: "test",
      agent: "installed-tools",
    },
  },
};
async function render(values) {
  const dir = await mkdtemp(join(tmpdir(), "ows-chart-"));
  try {
    const p = join(dir, "values.json");
    await writeFile(p, JSON.stringify({ ...base, ...values }));
    return await run("helm", [
      "template",
      "test",
      resolve("deploy/service/chart"),
      "-f",
      p,
    ]);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
function deployment(out, role) {
  return out
    .split("\n---")
    .find(
      (s) => s.includes("kind: Deployment") && s.includes(`name: test-${role}`),
    );
}
test("recordings stay opt-in and a separate connection uses an independently mounted secret",async()=>{
  const off=await render({});assert.doesNotMatch(off,/mountPath: \/run\/observability|"database_env": "OW_OBSERVABILITY_URL"/);
  const same=await render({config:{...base.config,schema:"ow_execution",observability:{}}});
  assert.match(same,/ow_observability/);assert.doesNotMatch(same,/mountPath: \/run\/observability/);
  const separate=await render({config:{...base.config,schema:"ow_execution",observability:{retention_days:14}},secrets:{observability:"recording-db"}});
  assert.match(separate,/"database_env": "OW_OBSERVABILITY_URL"/);assert.match(separate,/"database_ca": "\/run\/observability\/ca.crt"/);
  for(const role of ["api","worker","controller","platform"]){const d=deployment(separate,role);assert.match(d,/mountPath: \/run\/observability, readOnly: true/);assert.match(d,/secretName: recording-db/);}
  const bootstrap=separate.split("\n---").find(s=>s.includes("kind: Job")&&s.includes("name: test-bootstrap"));assert.match(bootstrap,/mountPath: \/run\/observability/);
  for(const values of [
    {secrets:{observability:"recording-db"}},
    {config:{...base.config,schema:"ow_same",observability:{schema:"ow_same"}}},
    {config:{...base.config,observability:{database:{database_env:"UNMOUNTED"}}}},
    {config:{...base.config,observability:{database:{database_loopback_plaintext:true}}},secrets:{observability:"db"}}
  ])await assert.rejects(()=>render(values));
});
test("service roles separate gateway, model, bootstrap and Kubernetes authority", async () => {
  const out = await render({ secrets: { model: "model-key" } });
  assert.equal((out.match(/kind: Deployment/g) || []).length, 4);
  const api = deployment(out, "api"),
    worker = deployment(out, "worker"),
    platform = deployment(out, "platform");
  assert.match(api, /automountServiceAccountToken: false/);
  assert.doesNotMatch(
    api,
    /mountPath: \/openshell|mountPath: \/models|mountPath: \/bootstrap/,
  );
  assert.match(worker, /mountPath: \/openshell/);
  assert.match(worker, /mountPath: \/models/);
  assert.match(worker, /automountServiceAccountToken: false/);
  assert.match(platform, /automountServiceAccountToken: true/);
  assert.doesNotMatch(platform, /mountPath: \/openshell|mountPath: \/models/);
  assert.match(out, /resources: \[deployments\/scale\]/);
  assert.match(out, /resourceNames: \[test-worker\]/);
  assert.doesNotMatch(out, /resources: \[secrets\]|verbs: \[.*create/);
  assert.match(out, /subPath: config.json/);
  assert.doesNotMatch(out, /mountPath: \/run\/service,/);
  assert.match(out, /subPath: admin-token/);
  assert.match(out, /labels: \{app: test-bootstrap\}/);
  assert.match(out, /readOnlyRootFilesystem: true/);
});
test("Helm does not overwrite manual/HPA capacity; only GitOps owns replicas", async () => {
  for (const ownership of ["manual", "autoscaled", "gitops"]) {
    const worker = deployment(
      await render({ worker: { ownership, replicas: 3 } }),
      "worker",
    );
    const beforeTemplate = worker.split("  template:")[0];
    if (ownership === "gitops") assert.match(beforeTemplate, /replicas: 3/);
    else assert.doesNotMatch(beforeTemplate, /\n  replicas:/);
  }
});
test("S3 identity is assigned only to API/worker, with no Kubernetes API role", async () => {
  const out = await render({
    config: {
      ...base.config,
      artifacts: {
        kind: "s3",
        deployment: "ows-test",
        bucket: "test-bucket",
        region: "us-west-2",
      },
    },
  });
  assert.match(out, /kind: ServiceAccount\nmetadata: \{name: test-artifacts\}/);
  for (const role of ["api", "worker"])
    assert.match(deployment(out, role), /serviceAccountName: test-artifacts/);
  for (const role of ["controller", "platform"])
    assert.doesNotMatch(
      deployment(out, role),
      /serviceAccountName: test-artifacts/,
    );
  assert.doesNotMatch(
    out,
    /subjects:.*test-artifacts|roleRef:.*test-artifacts/,
  );
});
test("artifact content streams with its own bounded limit while JSON stays small", async () => {
  const out = await render({});
  assert.match(out, /client_max_body_size 160k/);
  const content = out.match(/location ~ \^\/\(v1\/\)\?sessions\/[\s\S]*?\n\s*location \//)?.[0];
  assert.ok(content, "Missing scoped artifact content location");
  assert.match(content, /artifacts\/\[A-Za-z0-9_-\]\+\/content\$/);
  assert.match(content, /client_max_body_size 64m/);
  assert.match(content, /proxy_request_buffering off/);
  assert.match(content, /client_body_timeout 120s/);
});
test("Azure artifact identity has one explicit projection confined to API and worker", async () => {
  const artifacts = {
    kind: "azure", deployment: "ows-test", account: "owteststorage", container: "artifacts",
    tenant_id: "11111111-1111-4111-8111-111111111111",
    client_id: "22222222-2222-4222-8222-222222222222",
    federated_token_file: "/var/run/secrets/azure/tokens/azure-identity-token",
  };
  const out = await render({config: {...base.config, artifacts}});
  const sa = out.split("\n---").find(s => s.includes("kind: ServiceAccount") && s.includes("name: test-artifacts"));
  assert.match(sa, /azure.workload.identity\/client-id: "22222222-2222-4222-8222-222222222222"/);
  assert.match(sa, /azure.workload.identity\/tenant-id: "11111111-1111-4111-8111-111111111111"/);
  assert.match(sa, /automountServiceAccountToken: false/);
  for (const role of ["api", "worker"]) {
    const d = deployment(out, role);
    assert.match(d, /serviceAccountName: test-artifacts/);
    assert.match(d, /automountServiceAccountToken: false/);
    assert.match(d, /azure.workload.identity\/use: "true"/);
    assert.match(d, /azure.workload.identity\/skip-containers: "harness(?:;tls)?"/);
    assert.match(d, /name: AZURE_FEDERATED_TOKEN_FILE/);
    assert.match(d, /mountPath: \/var\/run\/secrets\/azure\/tokens, readOnly: true/);
    assert.match(d, /audience: api:\/\/AzureADTokenExchange/);
    assert.match(d, /expirationSeconds: 3600/);
    assert.equal((d.match(/name: azure-artifact-token\n/g) || []).length, 1);
    if (role === "api") assert.doesNotMatch(d.split("- name: tls\n")[1].split("      volumes:")[0], /azure|AZURE/);
  }
  for (const role of ["controller", "platform"]) assert.doesNotMatch(deployment(out, role), /azure-artifact-token|AZURE_|azure.workload.identity|serviceAccountName: test-artifacts/);
  const bootstrap = out.split("\n---").find(s => s.includes("kind: Job") && s.includes("name: test-bootstrap"));
  assert.doesNotMatch(bootstrap, /azure-artifact-token|AZURE_|azure.workload.identity|serviceAccountName: test-artifacts/);
  assert.doesNotMatch(out, /subjects:.*test-artifacts|roleRef:.*test-artifacts/);
  for (const change of [{deployment:"wrong/scope"},{account:"wrong.account"},{container:"bad--container"},{client_id:"not-a-uuid"},{tenant_id:"not-a-uuid"},{client_id:"00000000-0000-0000-0000-000000000000"},{federated_token_file:"/tmp/other-token"},{endpoint:"https://untrusted.example"},{client_secret:"unsupported"}]) {
    await assert.rejects(() => render({config:{...base.config, artifacts:{...artifacts,...change}}}));
  }
});
test("invalid ownership, mutable image and inadequate drain grace fail rendering", async () => {
  await assert.rejects(render({ worker: { ownership: "mixed" } }));
  await assert.rejects(render({ image: "example:latest" }));
  await assert.rejects(
    render({
      config: {
        service: { ...base.config.service, legacy_compatibility: true },
      },
    }),
  );
  await assert.rejects(
    render({
      worker: { drainSeconds: 90 },
      terminationGracePeriodSeconds: 100,
    }),
  );
});
test("management UI has no execution, database, or cluster authority", async () => {
  const uiValues = {
    enabled: true,
    image: "ui@sha256:" + "b".repeat(64),
    publicOrigin: "https://manage.example.test",
    serviceUrl: "https://test-api.default.svc",
    caSecret: "api-public-ca",
  };
  const out = await render({ managementUI: uiValues });
  const ui = deployment(out, "ui");
  assert.match(ui, /automountServiceAccountToken: false/);
  assert.match(ui, /replicas: 1/);
  assert.match(ui, /type: Recreate/);
  assert.match(ui, /NODE_EXTRA_CA_CERTS/);
  assert.match(ui, /readOnlyRootFilesystem: true/);
  assert.doesNotMatch(
    ui,
    /database-url|pg-ca|openshell|model-key|bootstrap|serviceAccountName/,
  );
  await assert.rejects(
    render({ managementUI: { ...uiValues, image: "ui:latest" } }),
  );
  await assert.rejects(
    render({
      managementUI: { ...uiValues, publicOrigin: "http://manage.example.test" },
    }),
  );
  await assert.rejects(
    render({ managementUI: { ...uiValues, serviceUrl: "http://test-api" } }),
  );
});
test("exporter authentication mounts only into harness roles and remains opt-in", async () => {
  const off = await render({});
  assert.doesNotMatch(off, /mountPath: \/run\/telemetry/);
  const out = await render({
    secrets: { telemetry: "export-auth" },
    managementUI: {
      enabled: true,
      image: "ui@sha256:" + "b".repeat(64),
      publicOrigin: "https://manage.example.test",
      serviceUrl: "https://test-api.default.svc",
    },
  });
  for (const role of ["api", "worker", "controller", "platform"]) {
    const d = deployment(out, role);
    assert.match(d, /mountPath: \/run\/telemetry, readOnly: true/);
    assert.match(d, /secretName: export-auth/);
  }
  const ui = deployment(out, "ui");
  assert.doesNotMatch(ui, /export-auth|\/run\/telemetry/);
  const bootstrap = out
    .split("\n---")
    .find((s) => s.includes("kind: Job") && s.includes("name: test-bootstrap"));
  assert.match(bootstrap, /OW_TELEMETRY_AUTH/);
  assert.match(bootstrap, /secretName: export-auth/);
});
