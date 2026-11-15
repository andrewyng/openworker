import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, readFile, stat, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  init,
  load,
  json,
  launch,
  until,
  stopBudgetMs,
  workerLifecycle,
} from "./stack.mjs";
test("local worker supervisor preserves the full drain and cleanup budget", () => {
  assert.equal(stopBudgetMs("worker-a"), 360000);
  assert.equal(stopBudgetMs("api-a"), 35000);
  assert.ok(
    stopBudgetMs("worker-b") >
      (workerLifecycle.drainSeconds + workerLifecycle.cleanupSeconds) * 1000,
  );
});
test("private generated state is scoped, non-overwriting and telemetry optional", async () => {
  const parent = await mkdtemp(join(tmpdir(), "ow-stack-")),
    root = join(parent, "state"),
    connection = join(parent, "connection.json");
  try {
    await writeFile(
      connection,
      JSON.stringify({
        runner: "/test/runner",
        openshell: { identity: "test" },
      }),
    );
    await assert.rejects(init(root, connection, true, 8000));
    const m = await init(root, connection, false, 1000);
    assert.equal(m.ports.console, 55340);
    assert.equal((await load(root)).project, m.project);
    const config = await json(join(root, "reference.json"));
    assert.equal(config.telemetry, undefined);
    assert.equal(config.operational_metrics, undefined);
    const consoleConfig = await json(join(root, "console.json"));
    assert.equal(consoleConfig.grafana_url, null);
    const key = await readFile(consoleConfig.access_key_file, "utf8"),
      api = await readFile(consoleConfig.api_token_file, "utf8");
    assert.notEqual(key, api);
    assert.notEqual(consoleConfig.api_token_file, config.api_token_file);
    assert.equal(
      (await json(join(root, "private/observer-spec.json"))).role,
      "observer",
    );
    assert.equal((await stat(consoleConfig.access_key_file)).mode & 0o077, 0);
    assert.ok(
      !(await readFile(join(root, "reference.json"), "utf8")).includes(api),
    );
    await assert.rejects(init(root, connection));
    assert.equal(await readFile(consoleConfig.api_token_file, "utf8"), api);
  } finally {
    await rm(parent, { recursive: true });
  }
});
test("observability provision matches explicit deployment and two worker slots", async () => {
  const parent = await mkdtemp(join(tmpdir(), "ow-stack-")),
    root = join(parent, "state"),
    connection = join(parent, "connection.json");
  try {
    await writeFile(
      connection,
      JSON.stringify({
        runner: "/test/runner",
        openshell: { identity: "test" },
      }),
    );
    const m = await init(root, connection, true);
    const config = await json(join(root, "reference.json"));
    assert.equal(config.telemetry.deployment, m.id);
    assert.deepEqual(config.operational_metrics, {});
    const alerts = await json(join(root, "operations-alerts.json"));
    assert.ok(
      alerts.groups[0].rules.every((r) => r.labels.deployment === m.id),
    );
    assert.equal(
      alerts.groups[0].rules.find((r) => r.uid.endsWith("-capacity")).data[1]
        .model.expression,
      "$A < 2",
    );
  } finally {
    await rm(parent, { recursive: true });
  }
});
test(
  "startup cancellation closes owned children before returning",
  { skip: !process.env.OW_STACK_STARTUP_TEST_STATE },
  async () => {
    const m = await load(process.env.OW_STACK_STARTUP_TEST_STATE),
      abort = new AbortController();
    const starting = launch(m, abort.signal).then(
      (result) => ({ result }),
      (error) => ({ error }),
    );
    try {
      await until(
        async () =>
          (
            await fetch(`http://127.0.0.1:${m.ports.apiA}/readyz`, {
              signal: AbortSignal.timeout(300),
            })
          ).status === 401,
        10000,
      );
      abort.abort();
      const outcome = await starting;
      if (outcome.result) {
        await outcome.result.close();
        assert.fail("Fixture did not interrupt startup");
      }
      assert.equal(outcome.error.name, "AbortError");
      for (const port of [m.ports.apiA, m.ports.apiB, m.ports.console])
        await assert.rejects(
          fetch(`http://127.0.0.1:${port}/`, {
            signal: AbortSignal.timeout(500),
          }),
        );
    } finally {
      abort.abort();
      const outcome = await starting;
      await outcome.result?.close();
    }
  },
);
