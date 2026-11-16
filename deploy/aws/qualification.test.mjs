import { test } from "node:test";
import assert from "node:assert/strict";
import {
  deploymentPods,
  hasFixtureWorker,
  planTool,
  postgresEnvironment,
} from "./qualification-lib.mjs";
test("non-root PostgreSQL helpers get an explicit libpq user and decoded password", () => {
  const env = postgresEnvironment(
    "postgresql://operator:p%40ss%2Fword@db.example:5432/openworker",
  );
  assert.equal(env.PGUSER, "operator");
  assert.equal(env.PGPASSWORD, "p@ss/word");
  assert.equal(env.PGDATABASE, "openworker");
  assert.equal(env.PGHOST, "db.example");
  assert.throws(() =>
    postgresEnvironment("postgresql://operator:pass@db.example/other"),
  );
  assert.throws(() =>
    postgresEnvironment(
      "postgresql://operator:pass@db.example/openworker?sslmode=disable",
    ),
  );
});
test("finished admin jobs do not prevent deployment worker removal checks", () => {
  const pod = (kind) => ({ metadata: { ownerReferences: [{ kind }] } });
  const worker = pod("ReplicaSet");
  assert.deepEqual(deploymentPods([pod("Job"), worker, { metadata: {} }]), [
    worker,
  ]);
});
test("same bundle live-model worker is not a scripted fixture worker", () => {
  const worker = (scripted, model, draining = false) => ({
    selector: { scripted, fallback_model: model },
    draining,
  });
  assert.equal(
    hasFixtureWorker({ workers: [worker(false, "gpt-6-sol")] }),
    false,
  );
  assert.equal(
    hasFixtureWorker({ workers: [worker(true, "fixture", true)] }),
    false,
  );
  assert.equal(
    hasFixtureWorker({
      workers: [worker(false, "gpt-6-sol"), worker(true, "fixture")],
    }),
    true,
  );
});
test("fixture prompts preserve exact tool arguments", () => {
  const args = { path: "a.txt", content: 'quote " and newline\n' };
  assert.deepEqual(
    JSON.parse(
      planTool("write_file", args).slice("TEST_RESOURCE_GRANTS ".length),
    ),
    [{ name: "write_file", arguments: args }],
  );
});
