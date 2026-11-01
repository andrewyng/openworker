import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { localValues } from "./local-values.mjs";
import { run, serviceConfig } from "./stack.mjs";
const runtime = {
  imageRef: "example/service@sha256:" + "a".repeat(64),
  runnerImage: "example/runner@sha256:" + "b".repeat(64),
};
test("local live-model values mount only a worker secret reference; scripted profiles do not", () => {
  const input = {
    scripted: false,
    model_provider: "anthropic",
    openshell: { image: "replace" },
    model_connections: [
      {
        id: "primary",
        provider: "anthropic",
        identity: "test",
        key_env: "ANTHROPIC_API_KEY",
      },
    ],
  };
  const live = localValues(input, runtime);
  assert.equal(live.secrets.model, "ow-service-model");
  assert.equal(input.openshell.image, "replace");
  assert.equal(live.config.openshell.image, runtime.runnerImage);
  assert.equal(live.config.runner, "/opt/openworker/openworker");
  assert.equal(
    localValues({ ...input, scripted: true }, runtime).secrets.model,
    "",
  );
});
test("rendered live local profile mounts model data only into the worker", async () => {
  const values = localValues(
    {
      service: {
        id: "test-service",
        namespace: "test",
        agent: "installed-tools",
      },
      scripted: false,
      model_provider: "anthropic",
      openshell: { image: "replace" },
      model_connections: [
        {
          id: "primary",
          provider: "anthropic",
          identity: "test",
          key_env: "ANTHROPIC_API_KEY",
        },
      ],
    },
    runtime,
  );
  const dir = await mkdtemp(join(tmpdir(), "ows-live-model-"));
  try {
    const path = join(dir, "values.json");
    await writeFile(path, JSON.stringify(values));
    const out = await run("helm", [
      "template",
      "test",
      resolve("deploy/service/chart"),
      "-f",
      path,
    ]);
    const docs = out.split("\n---");
    const worker = docs.find(
      (x) => x.includes("kind: Deployment") && x.includes("name: test-worker"),
    );
    assert.match(worker, /mountPath: \/models, readOnly: true/);
    assert.match(worker, /secretName: ow-service-model/);
    for (const role of ["api", "controller", "platform"]) {
      const doc = docs.find(
        (x) =>
          x.includes("kind: Deployment") && x.includes(`name: test-${role}`),
      );
      assert.doesNotMatch(doc, /mountPath: \/models/);
    }
    assert.doesNotMatch(out, /sk-ant-|modelKeyFile/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("local initialization retains explicit recording configuration without enabling it by default", () => {
  const base = {scripted:true,openshell:{endpoint:"https://gateway.example:8080",image:"runner@sha256:"+"c".repeat(64)}};
  assert.equal(serviceConfig(base,"ows-test").observability,undefined);
  const observability = {schema:"ow_observability",queue_capacity:1024,retention_days:7};
  const c=serviceConfig({...base,observability},"ows-test");
  assert.deepEqual(c.observability,observability);
  c.observability.retention_days=30;
  assert.equal(observability.retention_days,7);
});
test("local initialization preserves operator Git grants without implicitly selecting one",()=>{
  const base={scripted:true,openshell:{endpoint:"https://gateway.example:8080",image:"runner@sha256:"+"c".repeat(64)}};
  assert.equal(serviceConfig(base,"ows-test").openshell.git_grants,undefined);
  const grant={provider:{name:"github",provider_id:"original",profile_id:"github",profile_workspace:"",profile_digest:"a".repeat(64)},scope:{host:"github.com",repositories:["org/repo"]}};
  const c=serviceConfig({...base,openshell:{...base.openshell,git_grants:{"github@1":grant}}},"ows-test");
  assert.deepEqual(c.openshell.git_grants,{"github@1":grant});
  assert.equal(c.openshell.selected_git_grant,undefined);
  c.openshell.git_grants["github@1"].scope.repositories.push("changed/repo");
  assert.deepEqual(grant.scope.repositories,["org/repo"]);
});
