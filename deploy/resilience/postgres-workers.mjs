// Each fault case owns a fresh database and Linux process tree. A failed case
// cannot leave actors, catalog state or signal handlers in the next case.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, writeFile, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
const exec = promisify(execFile);
const [directory, image, postgres] = process.argv.slice(2);
assert.match(image ?? "", /^sha256:[a-f0-9]{64}$/);
assert.match(postgres ?? "", /^postgres:16-alpine@sha256:[a-f0-9]{64}$/);
const root = resolve(directory, "worker-cases");
await mkdir(root, { mode: 0o700 });
let stopping = false,
  active;
process.once("SIGTERM", () => {
  stopping = true;
  active?.kill("SIGTERM");
});
const docker = async (args, timeout = 120000) => {
  const pending = exec("docker", args, { timeout, maxBuffer: 4194304 });
  active = pending.child;
  try {
    return await pending;
  } finally {
    active = null;
  }
};
const inspect = async (name) => {
  try {
    return JSON.parse((await docker(["inspect", name])).stdout)[0];
  } catch (e) {
    if (/No such (object|container):/.test(e.stderr ?? "")) return null;
    throw e; // An unavailable daemon is not evidence of deletion.
  }
};
const listed = await docker([
  "run",
  "--pull=never",
  "--rm",
  "--network",
  "none",
  "--entrypoint",
  "/out/worker-recovery",
  image,
  "--list",
  "--ignored",
]);
const cases = listed.stdout
  .split("\n")
  .filter((s) => s.endsWith(": test"))
  .map((s) => s.slice(0, -6))
  .filter(
    (s) =>
      !s.includes(
        "mixed_versions_keep_native_identity_pinned_and_old_pool_can_resume",
      ) && !s.includes("bounded_load_matrix"),
  );
assert.equal(
  cases.length,
  15,
  "Review the worker catalog before changing fault coverage",
);
assert(
  cases.includes(
    "resilience_streaming_worker_death_preserves_budget_and_cursor_without_tool_replay",
  ),
);
const report = {
  passed: false,
  cases: [],
  cleanup: {
    allOwnedContainersRemoved: true,
    allGeneratedSecretsRemoved: true,
  },
};
try {
  for (const [index, test] of cases.entries()) {
    assert(
      !stopping,
      "Qualification cancelled; no new fixture will be created",
    );
    const dir = join(root, String(index).padStart(2, "0"));
    await mkdir(dir, { mode: 0o700 });
    const id = randomUUID().replaceAll("-", ""),
      pg = "ow-resilience-pg-case-" + id,
      worker = "ow-resilience-worker-case-" + id;
    const label = "openworker.resilience",
      password = randomBytes(24).toString("hex");
    const secret = join(dir, "password"),
      env = join(dir, "worker-env");
    const row = { name: test, passed: false, cleanup: false };
    report.cases.push(row);
    await writeFile(secret, password, { mode: 0o600, flag: "wx" });
    await writeFile(
      env,
      `OW_PG_TEST_URL=postgresql://openworker:${password}@127.0.0.1:5432/execution\n`,
      { mode: 0o600, flag: "wx" },
    );
    await writeFile(
      join(dir, "intent.json"),
      JSON.stringify({ id, pg, worker, image, postgres }),
      { mode: 0o600, flag: "wx" },
    );
    try {
      await docker([
        "run",
        "--pull=never",
        "--rm",
        "-d",
        "--name",
        pg,
        "--label",
        `${label}=${id}`,
        "--memory",
        "1g",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,size=1g",
        "-e",
        "POSTGRES_USER=openworker",
        "-e",
        "POSTGRES_DB=execution",
        "-e",
        "POSTGRES_PASSWORD_FILE=/run/secrets/password",
        "--mount",
        `type=bind,source=${secret},target=/run/secrets/password,readonly`,
        postgres,
        "-c",
        "shared_buffers=32MB",
      ]);
      let ready = false;
      for (let n = 0; n < 120; n++) {
        assert(!stopping, "Qualification cancelled before worker admission");
        try {
          await docker([
            "exec",
            pg,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            "openworker",
            "-d",
            "execution",
          ]);
          ready = true;
          break;
        } catch {
          await new Promise((r) => setTimeout(r, 250));
        }
      }
      assert(ready, "Owned PostgreSQL listener did not become ready");
      assert(!stopping, "Qualification cancelled before worker admission");
      let output,
        code = 0;
      try {
        output = await docker(
          [
            "run",
            "--pull=never",
            "--rm",
            "--name",
            worker,
            "--label",
            `${label}=${id}`,
            "--network",
            `container:${pg}`,
            "--memory",
            "2g",
            "--env-file",
            env,
            "--entrypoint",
            "/out/worker-recovery",
            image,
            "--ignored",
            "--exact",
            test,
            "--nocapture",
          ],
          180000,
        );
      } catch (e) {
        code = e.code;
        output = { stdout: e.stdout ?? "", stderr: e.stderr ?? "" };
      }
      const log = output.stdout + output.stderr;
      await writeFile(join(dir, "worker.log"), log, {
        mode: 0o600,
        flag: "wx",
      });
      // Preserve Rust's count/name evidence for the parent runner, without keys.
      process.stdout.write(log.replaceAll(password, "[redacted]"));
      row.passed = code === 0 && /test result: ok\. 1 passed/.test(log);
    } finally {
      try {
        const pgState = await inspect(pg);
        if (pgState) {
          assert.equal(pgState.Config.Labels[label], id);
          const log = await docker(["logs", pg]);
          await writeFile(join(dir, "postgres.log"), log.stdout + log.stderr, {
            mode: 0o600,
            flag: "wx",
          });
          await writeFile(
            join(dir, "postgres-state.json"),
            JSON.stringify({
              state: pgState.State,
              memoryLimit: pgState.HostConfig.Memory,
            }),
            { mode: 0o600, flag: "wx" },
          );
          row.diagnosticsComplete = true;
        }
      } catch {
        row.diagnosticsComplete = false;
      } // Never skip owned cleanup because log capture failed.
      for (const name of [worker, pg]) {
        const v = await inspect(name);
        if (v) {
          assert.equal(v.Config.Labels[label], id);
          await docker(["rm", "-f", v.Id]);
        }
        assert.equal(await inspect(name), null);
      }
      await Promise.all([secret, env].map((p) => unlink(p)));
      row.cleanup = true;
    }
    assert(
      row.diagnosticsComplete,
      "Owned database diagnostics unavailable; inspect private fixture intent",
    );
    assert(
      row.passed,
      `Isolated worker fault case failed: ${test}; inspect private evidence`,
    );
  }
  report.passed = true;
} finally {
  report.cleanup.allOwnedContainersRemoved = report.cases.every(
    (c) => c.cleanup,
  );
  report.cleanup.allGeneratedSecretsRemoved = report.cases.every(
    (c) => c.cleanup,
  );
  await writeFile(join(root, "report.json"), JSON.stringify(report, null, 2), {
    mode: 0o600,
    flag: "wx",
  });
}
