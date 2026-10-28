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
