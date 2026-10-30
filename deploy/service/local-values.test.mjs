import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { localValues } from "./local-values.mjs";
import { run } from "./stack.mjs";
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
