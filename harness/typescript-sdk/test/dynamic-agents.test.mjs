import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { Harness } from "../dist/index.js";

const executable = process.env.OW_TS_TEST_HOST;
assert.ok(executable, "Set OW_TS_TEST_HOST to the real Linux sdk-test-host");
for (const database of ["sqlite", "postgres"]) {
  test(
    `dynamic agents on ${database}: register, approve, restart, model override`,
    {
      skip: database === "postgres" && !process.env.OW_PG_TEST_URL,
      timeout: 90_000,
    },
    async (t) => {
      const root = await mkdtemp(join(tmpdir(), "ow-dynamic-ts-"));
      const workspace = join(root, "work");
      await mkdir(workspace);
      const storage =
        database === "postgres"
          ? {
              type: "postgres",
              connectionEnv: "OW_PG_TEST_URL",
              schema: "ow_dynamic_ts_" + randomUUID().replaceAll("-", ""),
              loopbackPlaintext: true,
            }
          : { type: "sqlite" };
      if (database === "postgres") {
        const config = join(root, "migrate.json");
        await writeFile(
          config,
          JSON.stringify({
            state_dir: join(root, "state"),
            runner: executable,
            coworkers: [],
            agents: [],
            sandbox: "off",
            storage: {
              kind: "postgres",
              connection_env: "OW_PG_TEST_URL",
              schema: storage.schema,
              loopback_plaintext: true,
            },
          }),
        );
        execFileSync(executable, ["sdk-test-migrate", config]);
      }
      const options = {
        executable,
        stateDir: join(root, "state"),
        sandbox: "off",
        scripted: true,
        storage,
      };
      let harness = await Harness.start(options);
      t.after(async () => {
        await harness.shutdown();
        await rm(root, { recursive: true, force: true });
      });
      assert.deepEqual(await harness.listAgents(), []);
      await harness.setModelProvider({
        name: "OpenAI",
        api: "responses",
        credentials: { apiKey: "not-a-real-api-key", storage: "memory" },
      });
      const definition = {
        name: "builder",
        instructions: "Use the probe.",
        tools: ["sdk.probe"],
      };
      const builder = harness.createAgent(definition);
      await builder.ready();
      assert.deepEqual(await harness.listAgents(), ["builder"]);
      await harness.createAgent(definition).ready();
      await assert.rejects(
        harness.createAgent({ ...definition, instructions: "Changed" }).ready(),
        { code: "Remote" },
      );
      const session = await builder.newSession({ workspace });
      const ref = session.reference();
      const prompt =
        "TEST_RESOURCE_GRANTS " +
        JSON.stringify([{ name: "sdk__probe", arguments: { block: false } }]);
      const result = await session.run(prompt);
      assert.equal(result.status, "interrupted");
      await assert.rejects(readFile(join(workspace, "proof.txt")), {
        code: "ENOENT",
      });
      await session.respond(result.interruptions[0], { decision: "once" });
      assert.equal((await session.resume()).status, "completed");
      assert.equal(
        await readFile(join(workspace, "proof.txt"), "utf8"),
        "called\n",
      );
      const override = await builder.newSession({
        workspace,
        model: { provider: "OpenAI", name: "chosen-fixture" },
      });
      assert.equal((await override.snapshot()).session.model, "chosen-fixture");
      const defaults = harness.createAgent({
        ...definition,
        name: "defaults",
        defaultModel: { provider: "OpenAI", name: "default-fixture" },
      });
      await defaults.ready();
      assert.equal(
        (await (await defaults.newSession({ workspace })).snapshot()).session
          .model,
        "default-fixture",
      );
      await assert.rejects(
        defaults.resumeSession({ ...ref, agent: "defaults" }),
        { code: "InvalidInput" },
      );
      await harness.shutdown();
      harness = await Harness.start(options);
      assert.deepEqual(await harness.listAgents(), ["builder", "defaults"]);
      const restored = await harness.createAgent(definition).resumeSession(ref);
      assert.equal((await restored.snapshot()).session.coworker, "builder");
      assert.equal(
        await readFile(join(workspace, "proof.txt"), "utf8"),
        "called\n",
      );
    },
  );
}
